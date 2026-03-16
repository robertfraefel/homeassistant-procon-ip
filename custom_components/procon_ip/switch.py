"""
Switch platform for the ProCon.IP integration.

Each active relay channel is exposed as a ``SwitchEntity`` that toggles between
manual-on and manual-off.  This complements the ``SelectEntity`` in
``select.py`` which additionally offers the *auto* mode.

Turning a switch **on** sends ``manual + on`` to the device.
Turning a switch **off** sends ``manual + off`` to the device.

If the relay is currently in *auto* mode the switch reports its **physical**
state (on/off) as determined by the device's internal schedule, so the HA UI
always reflects what the relay is actually doing.
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ALL_RELAY_COLS,
    DOMAIN,
    RELAY_BIT_ON,
    RELAY_STATE_OFF,
    RELAY_STATE_ON,
)
from .coordinator import ProConIPCoordinator

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create Switch entities for every active relay channel."""
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPRelaySwitch] = []
    for col in ALL_RELAY_COLS:
        if not data.is_active(col):
            continue
        entities.append(ProConIPRelaySwitch(coordinator, entry, col))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entity class
# ---------------------------------------------------------------------------

class ProConIPRelaySwitch(CoordinatorEntity[ProConIPCoordinator], SwitchEntity):
    """On/Off switch for a single ProCon.IP relay."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._col = col_index

        self._attr_unique_id = f"{entry.entry_id}_switch_{col_index}"
        self._attr_device_info = coordinator.device_info

        label = coordinator.data.names[col_index].strip()
        self._attr_name = f"{label} Switch"

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        """Return True if the relay is physically on (regardless of auto/manual)."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.raws[self._col] if self._col < len(self.coordinator.data.raws) else 0
        return bool(raw & RELAY_BIT_ON)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the relay on (manual mode)."""
        await self.coordinator.async_set_relay(self._col, RELAY_STATE_ON)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the relay off (manual mode)."""
        await self.coordinator.async_set_relay(self._col, RELAY_STATE_OFF)
