"""
Binary sensor platform for the ProCon.IP integration.

This module creates one ``ProConIPBinarySensor`` entity for each digital input
channel (CSV columns 24–27) whose unit is ``"--"`` (dimensionless on/off
signal).

Digital input channels with a numeric unit (e.g. Durchfluss in ``"l/h"``)
are **not** handled here; they become numeric ``SensorEntity`` objects in
``sensor.py`` instead.

Examples of channels that become binary sensors (from a typical installation):

- **TASTER2** (col 25) – physical button / switch input
- **TASTER3** (col 26) – physical button / switch input
- **Poolabdeckung** (col 27) – pool cover position signal (open/closed)

State decoding
--------------
The raw integer value for a digital input is either 0 (inactive) or non-zero
(active).  ``is_on`` returns ``True`` whenever ``raw != 0``.

No device class is assigned by default because the physical meaning of each
channel depends entirely on how the user wired their installation.  Users can
override the device class in the HA entity settings if they want a specific
icon (e.g. ``door``, ``motion``, ``opening``).
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COL_RANGE_DIGITAL_INPUT, DOMAIN
from .coordinator import ProConIPCoordinator


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Create binary sensor entities for dimensionless digital input channels.

    Iterates over columns 24–27 (``COL_RANGE_DIGITAL_INPUT``), skips any that
    are labelled ``"n.a."`` or have a numeric unit (those go to ``sensor.py``),
    and registers a ``ProConIPBinarySensor`` for each remaining channel.

    Args:
        hass:              The Home Assistant instance.
        entry:             The config entry for this device.
        async_add_entities: Callback to register the new entities with HA.
    """
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPBinarySensor] = []
    for col in COL_RANGE_DIGITAL_INPUT:
        # Skip channels that are not physically connected
        if not data.is_active(col):
            continue
        unit_csv = data.units[col].strip() if col < len(data.units) else ""
        # Only create binary sensors for dimensionless signals (unit == "--")
        # Channels with a real numeric unit (e.g. l/h) belong in sensor.py
        if unit_csv != "--":
            continue
        entities.append(ProConIPBinarySensor(coordinator, entry, col))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entity class
# ---------------------------------------------------------------------------

class ProConIPBinarySensor(CoordinatorEntity[ProConIPCoordinator], BinarySensorEntity):
    """
    Binary sensor for one ProCon.IP digital input channel.

    Inherits from ``CoordinatorEntity`` so it is refreshed automatically
    whenever the coordinator publishes a new ``ProConIPData`` snapshot.

    No ``device_class`` is set because the physical meaning of each digital
    input depends on the installation wiring and is unknown to the integration.
    Users can set the device class manually in the HA entity settings.
    """

    # _attr_has_entity_name = True lets HA build the full name as
    # "<device name> <entity name>", e.g. "ProCon.IP Pool Controller TASTER2"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        """
        Initialise the binary sensor entity.

        Args:
            coordinator: The shared ``ProConIPCoordinator`` for this device.
            entry:       The config entry (used to build the unique ID).
            col_index:   0-based CSV column index of the digital input channel
                         (one of 24, 25, 26, or 27).
        """
        super().__init__(coordinator)
        self._col = col_index

        # "di_" prefix distinguishes binary sensor UIDs from sensor UIDs for
        # channels that share the same column index range
        self._attr_unique_id   = f"{entry.entry_id}_di_{col_index}"
        self._attr_device_info = coordinator.device_info

        # Entity name comes from the CSV label (e.g. "TASTER2", "Poolabdeckung")
        self._attr_name = coordinator.data.names[col_index].strip()

    # ------------------------------------------------------------------
    # Dynamic property – re-evaluated on every coordinator update
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        """
        Return ``True`` when the digital input is in the active/high state.

        The ProCon.IP sets the raw value to 0 when the input is inactive and
        to a non-zero integer (typically 1) when it is active.  Any non-zero
        value is therefore treated as ``True`` to be robust against firmware
        versions that use different active levels.

        Returns:
            ``True``  – input is active (raw != 0).
            ``False`` – input is inactive (raw == 0).
            ``None``  – no data received yet, or column out of range.
        """
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        if self._col >= len(data.raws):
            return None
        return data.raws[self._col] != 0
