"""Sensor entities for ProCon.IP."""
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
    COL_RANGE_CANISTER_CONSUMPTION,
    COL_RANGE_DIGITAL_INPUT,
    COL_RANGE_TIME,
    DOMAIN,
    UNIT_MAP,
    UNIT_PRECISION,
)
from .coordinator import ProConIPCoordinator

# Map from HA unit → SensorDeviceClass
_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    UnitOfTemperature.CELSIUS:        SensorDeviceClass.TEMPERATURE,
    UnitOfPressure.BAR:               SensorDeviceClass.PRESSURE,
    UnitOfElectricPotential.MILLIVOLT: SensorDeviceClass.VOLTAGE,
}

# Map from HA unit → SensorStateClass
_STATE_CLASS: dict[str, SensorStateClass] = {
    UnitOfTemperature.CELSIUS:        SensorStateClass.MEASUREMENT,
    UnitOfPressure.BAR:               SensorStateClass.MEASUREMENT,
    UnitOfElectricPotential.MILLIVOLT: SensorStateClass.MEASUREMENT,
    "pH":                              SensorStateClass.MEASUREMENT,
    PERCENTAGE:                        SensorStateClass.MEASUREMENT,
    "mL":                              SensorStateClass.TOTAL_INCREASING,
    "L/h":                             SensorStateClass.MEASUREMENT,
}

# Columns that are handled by other platforms (skip in sensor.py)
_SKIP_COLS = (
    set(COL_RANGE_TIME)          # internal timer, not useful
    | set(ALL_RELAY_COLS)        # → select.py
)


def _is_digital_input_binary(col: int, unit_csv: str) -> bool:
    """True for digital-input columns with no numeric unit (→ binary_sensor.py)."""
    return col in COL_RANGE_DIGITAL_INPUT and unit_csv.strip() == "--"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPSensor] = []
    for col in range(len(data.names)):
        if col in _SKIP_COLS:
            continue
        if not data.is_active(col):
            continue
        unit_csv = data.units[col].strip() if col < len(data.units) else ""
        if _is_digital_input_binary(col, unit_csv):
            continue   # handled by binary_sensor.py
        entities.append(ProConIPSensor(coordinator, entry, col))

    async_add_entities(entities)


class ProConIPSensor(CoordinatorEntity[ProConIPCoordinator], SensorEntity):
    """A single numeric sensor derived from one CSV column."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._col = col_index
        self._attr_unique_id = f"{entry.entry_id}_sensor_{col_index}"
        self._attr_device_info = coordinator.device_info

        data = coordinator.data
        self._attr_name = data.names[col_index].strip()

        unit_csv = data.units[col_index].strip() if col_index < len(data.units) else ""
        ha_unit = UNIT_MAP.get(unit_csv, unit_csv or None)

        self._attr_native_unit_of_measurement = ha_unit
        self._attr_device_class = _DEVICE_CLASS.get(ha_unit) if ha_unit else None
        self._attr_state_class = _STATE_CLASS.get(ha_unit) if ha_unit else None
        self._attr_suggested_display_precision = (
            UNIT_PRECISION.get(ha_unit, 2) if ha_unit else 2
        )

    @property
    def native_value(self) -> float | None:
        """Return the computed sensor value."""
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        if self._col >= len(data.values):
            return None
        return round(data.values[self._col], 6)
