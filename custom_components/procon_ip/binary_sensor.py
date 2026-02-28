"""Binary sensor entities for ProCon.IP digital inputs."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COL_RANGE_DIGITAL_INPUT, DOMAIN
from .coordinator import ProConIPCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities for digital inputs with unit '--'."""
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPBinarySensor] = []
    for col in COL_RANGE_DIGITAL_INPUT:
        if not data.is_active(col):
            continue
        unit_csv = data.units[col].strip() if col < len(data.units) else ""
        if unit_csv != "--":
            continue  # numeric digital inputs are handled by sensor.py
        entities.append(ProConIPBinarySensor(coordinator, entry, col))

    async_add_entities(entities)


class ProConIPBinarySensor(CoordinatorEntity[ProConIPCoordinator], BinarySensorEntity):
    """Binary sensor for a ProCon.IP digital input channel."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._col = col_index
        self._attr_unique_id = f"{entry.entry_id}_di_{col_index}"
        self._attr_device_info = coordinator.device_info
        self._attr_name = coordinator.data.names[col_index].strip()

    @property
    def is_on(self) -> bool | None:
        """Return True if the digital input is active (raw != 0)."""
        if self.coordinator.data is None:
            return None
        data = self.coordinator.data
        if self._col >= len(data.raws):
            return None
        return data.raws[self._col] != 0
