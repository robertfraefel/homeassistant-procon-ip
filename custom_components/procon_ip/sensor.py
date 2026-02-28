"""
Sensor platform for the ProCon.IP integration.

This module creates one ``ProConIPSensor`` entity for every active (non-n.a.)
CSV column that is *not* handled by another platform:

- Relay columns (16–23, 28–35) → ``select.py``
- Digital-input columns with unit ``"--"`` → ``binary_sensor.py``
- Time column (0) → skipped (internal processing timer, not meaningful)

For every remaining active column – temperatures, pH, Redox, analog channels,
pressure, flow rate, canister levels, consumption – a sensor is created with:

- ``native_unit_of_measurement`` mapped from the CSV unit via ``UNIT_MAP``
- ``device_class`` derived from the HA unit (temperature, pressure, voltage)
- ``state_class`` set to ``MEASUREMENT`` for instantaneous readings or
  ``TOTAL_INCREASING`` for cumulative counters (chemical consumption)
- ``suggested_display_precision`` from ``UNIT_PRECISION`` so the frontend
  rounds values to a sensible number of decimal places

Entity attributes are set once in ``__init__`` (static metadata) and only
``native_value`` is re-evaluated on each coordinator update (dynamic data).
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ALL_RELAY_COLS,
    COL_RANGE_DIGITAL_INPUT,
    COL_RANGE_TIME,
    DOMAIN,
    UNIT_MAP,
    UNIT_PRECISION,
)
from .coordinator import ProConIPCoordinator

# ---------------------------------------------------------------------------
# Module-level lookup tables
# ---------------------------------------------------------------------------

# Maps a HA unit string → SensorDeviceClass.
# Only units with a well-defined HA device class are listed; all other units
# get device_class=None (no icon/unit class enforcement by HA).
_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    UnitOfTemperature.CELSIUS:         SensorDeviceClass.TEMPERATURE,
    UnitOfPressure.BAR:                SensorDeviceClass.PRESSURE,
    UnitOfElectricPotential.MILLIVOLT: SensorDeviceClass.VOLTAGE,
}

# Maps a HA unit string → SensorStateClass.
# MEASUREMENT is used for instantaneous readings (temperature, pH, …).
# TOTAL_INCREASING is used for the chemical consumption counters which only
# ever grow (they are reset when the canister is refilled in the device UI,
# but that reset is not observable via the API, so TOTAL_INCREASING is the
# closest match and enables the HA statistics database to work correctly).
_STATE_CLASS: dict[str, SensorStateClass] = {
    UnitOfTemperature.CELSIUS:         SensorStateClass.MEASUREMENT,
    UnitOfPressure.BAR:                SensorStateClass.MEASUREMENT,
    UnitOfElectricPotential.MILLIVOLT: SensorStateClass.MEASUREMENT,
    "pH":                              SensorStateClass.MEASUREMENT,
    PERCENTAGE:                        SensorStateClass.MEASUREMENT,
    "mL":                              SensorStateClass.TOTAL_INCREASING,
    "L/h":                             SensorStateClass.MEASUREMENT,
}

# Columns belonging to other platforms that sensor.py must not touch
_SKIP_COLS: set[int] = (
    set(COL_RANGE_TIME)    # col 0: internal processing timer – not useful
    | set(ALL_RELAY_COLS)  # cols 16-23, 28-35: handled by select.py
)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _is_digital_input_binary(col: int, unit_csv: str) -> bool:
    """
    Return ``True`` when *col* is a dimensionless digital input.

    Digital input columns (24–27) can carry either a numeric value with a
    real unit (e.g. Durchfluss in ``"l/h"``) or a pure on/off signal with
    unit ``"--"``.  The latter type belongs to ``binary_sensor.py``.

    Args:
        col:      0-based CSV column index.
        unit_csv: The raw unit string from the CSV for that column.

    Returns:
        ``True`` when the column is in the digital-input range **and** its
        CSV unit is ``"--"`` (no numeric value).
    """
    return col in COL_RANGE_DIGITAL_INPUT and unit_csv.strip() == "--"


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Create sensor entities for all numeric, non-relay active CSV columns.

    Called by HA after ``async_setup_entry`` in ``__init__.py`` forwards
    platform setup.  Iterates over every CSV column, skips those belonging to
    other platforms or labelled ``"n.a."``, and registers a ``ProConIPSensor``
    for each remaining active column.

    Args:
        hass:              The Home Assistant instance.
        entry:             The config entry for this device.
        async_add_entities: Callback to register the new entities with HA.
    """
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPSensor] = []
    for col in range(len(data.names)):
        # Skip columns owned by other platforms or the useless timer column
        if col in _SKIP_COLS:
            continue
        # Skip channels that are not physically connected ("n.a." label)
        if not data.is_active(col):
            continue
        # Skip digital inputs that are pure on/off signals → binary_sensor.py
        unit_csv = data.units[col].strip() if col < len(data.units) else ""
        if _is_digital_input_binary(col, unit_csv):
            continue
        entities.append(ProConIPSensor(coordinator, entry, col))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entity class
# ---------------------------------------------------------------------------

class ProConIPSensor(CoordinatorEntity[ProConIPCoordinator], SensorEntity):
    """
    A single numeric measurement derived from one CSV column.

    Inherits from ``CoordinatorEntity`` so it is automatically updated
    whenever the coordinator publishes a new ``ProConIPData`` snapshot.

    The entity's static metadata (name, unit, device class, state class,
    precision) is resolved once in ``__init__`` from the first data snapshot
    and never changes.  Only ``native_value`` is re-evaluated on every update.

    Attributes set as ``_attr_*`` class/instance variables are read directly
    by HA and do not require property implementations.
    """

    # _attr_has_entity_name = True means HA will prepend the device name to
    # build the full entity name, e.g. "ProCon.IP Pool Controller Pool".
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        """
        Initialise the sensor entity.

        All static attributes are resolved here using the data that was already
        fetched by the coordinator during ``async_config_entry_first_refresh``.

        Args:
            coordinator: The shared ``ProConIPCoordinator`` for this device.
            entry:       The config entry (used to build the unique ID).
            col_index:   0-based CSV column index this sensor represents.
        """
        super().__init__(coordinator)
        self._col = col_index

        # Stable unique ID: survives renames and HA restarts
        self._attr_unique_id = f"{entry.entry_id}_sensor_{col_index}"

        # Group this entity under the device card for the ProCon.IP unit
        self._attr_device_info = coordinator.device_info

        data = coordinator.data

        # Entity name = the column label from the CSV (e.g. "Pool", "pH")
        self._attr_name = data.names[col_index].strip()

        # Translate the CSV unit string to a HA-compatible unit string
        unit_csv = data.units[col_index].strip() if col_index < len(data.units) else ""
        ha_unit  = UNIT_MAP.get(unit_csv, unit_csv or None)

        self._attr_native_unit_of_measurement = ha_unit

        # Device class drives the icon and unit-conversion support in HA
        self._attr_device_class = _DEVICE_CLASS.get(ha_unit) if ha_unit else None

        # State class enables long-term statistics in the HA recorder
        self._attr_state_class = _STATE_CLASS.get(ha_unit) if ha_unit else None

        # How many decimal places the frontend shows (does not affect storage)
        self._attr_suggested_display_precision = (
            UNIT_PRECISION.get(ha_unit, 2) if ha_unit else 2
        )

    # ------------------------------------------------------------------
    # Dynamic property – re-evaluated on every coordinator update
    # ------------------------------------------------------------------

    @property
    def native_value(self) -> float | None:
        """
        Return the current sensor reading.

        The value is pre-computed by ``_parse_csv`` as::

            offset + factor × raw

        We round to 6 significant decimal places to suppress floating-point
        noise (e.g. 22.499999999 → 22.5) while preserving meaningful
        precision.  HA's ``suggested_display_precision`` further rounds the
        displayed value in the frontend.

        Returns:
            The floating-point sensor value, or ``None`` if no data has been
            received yet or the column index is out of range.
        """
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        if self._col >= len(data.values):
            return None
        return round(data.values[self._col], 6)
