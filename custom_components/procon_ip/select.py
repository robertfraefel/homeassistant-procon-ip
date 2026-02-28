"""Select entities for ProCon.IP relay control (Auto / On / Off)."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ALL_RELAY_COLS, DOMAIN, RELAY_STATES
from .coordinator import ProConIPCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up relay select entities."""
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPRelaySelect] = []
    for col in ALL_RELAY_COLS:
        if not data.is_active(col):
            continue
        entities.append(ProConIPRelaySelect(coordinator, entry, col))

    async_add_entities(entities)


class ProConIPRelaySelect(CoordinatorEntity[ProConIPCoordinator], SelectEntity):
    """Select entity for one ProCon.IP relay: Auto / On / Off."""

    _attr_has_entity_name = True
    _attr_options = RELAY_STATES

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._col = col_index
        self._attr_unique_id = f"{entry.entry_id}_relay_{col_index}"
        self._attr_device_info = coordinator.device_info
        self._attr_name = coordinator.data.names[col_index].strip()

    @property
    def current_option(self) -> str | None:
        """Return current relay state."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get_relay_state(self._col)

    async def async_select_option(self, option: str) -> None:
        """Handle user selection and send the new state to the device."""
        if option not in RELAY_STATES:
            _LOGGER.error("Invalid relay option: %s", option)
            return
        await self.coordinator.async_set_relay(self._col, option)
