"""
Select platform for the ProCon.IP integration.

Each active relay channel on the ProCon.IP is exposed as a ``SelectEntity``
with three options: ``"auto"``, ``"on"``, and ``"off"``.

Why Select and not Switch?
--------------------------
A standard HA ``SwitchEntity`` only models two states (on / off).  ProCon.IP
relays have a third, important state – **auto** – where the device's internal
timer and sensor logic controls the relay independently.  Using a Select entity
preserves this third state and prevents users from accidentally overriding the
schedule when they only want to check the current mode.

Relay columns
-------------
Internal relays occupy CSV columns 16–23 (up to 8 relays).
External relays occupy CSV columns 28–35 (up to 8 optional additional relays).
Both ranges are iterated; columns labelled ``"n.a."`` are skipped.

State updates
-------------
The entity's ``current_option`` property delegates to
``ProConIPData.get_relay_state()``, which decodes the 2-bit raw value into a
state string.  When the user changes the Select, ``async_select_option``
calls ``ProConIPCoordinator.async_set_relay()`` which POSTs the new state to
``/usrcfg.cgi`` and requests an immediate coordinator refresh.
"""
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


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Create Select entities for every active relay channel.

    Iterates over all relay column indices (internal + external), skips those
    labelled ``"n.a."`` in the CSV, and registers a ``ProConIPRelaySelect``
    for each active relay.

    Args:
        hass:              The Home Assistant instance.
        entry:             The config entry for this device.
        async_add_entities: Callback to register the new entities with HA.
    """
    coordinator: ProConIPCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    entities: list[ProConIPRelaySelect] = []
    for col in ALL_RELAY_COLS:
        # Skip relay slots that are not physically wired ("n.a." label)
        if not data.is_active(col):
            continue
        entities.append(ProConIPRelaySelect(coordinator, entry, col))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entity class
# ---------------------------------------------------------------------------

class ProConIPRelaySelect(CoordinatorEntity[ProConIPCoordinator], SelectEntity):
    """
    Select entity representing one ProCon.IP relay with Auto / On / Off modes.

    Inherits from ``CoordinatorEntity`` so it is automatically refreshed
    whenever the coordinator publishes new data.

    Class attributes
    ----------------
    _attr_options : list[str]
        The fixed set of choices shown in the HA UI drop-down.
        Set to ``RELAY_STATES = ["auto", "on", "off"]``.
    _attr_has_entity_name : bool
        When ``True``, HA prepends the device name to form the full entity
        name (e.g. "ProCon.IP Pool Controller FilterPumpe N1").
    """

    _attr_has_entity_name = True
    # Declare the available options once at class level; they never change
    _attr_options = RELAY_STATES

    def __init__(
        self,
        coordinator: ProConIPCoordinator,
        entry: ConfigEntry,
        col_index: int,
    ) -> None:
        """
        Initialise the relay Select entity.

        Args:
            coordinator: The shared ``ProConIPCoordinator`` for this device.
            entry:       The config entry (used to build the unique ID).
            col_index:   0-based CSV column index of the relay (16–23 or
                         28–35).
        """
        super().__init__(coordinator)
        self._col = col_index

        # unique_id uses "relay_" prefix to avoid collisions with sensor UIDs
        self._attr_unique_id  = f"{entry.entry_id}_relay_{col_index}"
        self._attr_device_info = coordinator.device_info

        # Entity name comes from the CSV label (e.g. "FilterPumpe N1")
        self._attr_name = coordinator.data.names[col_index].strip()

    # ------------------------------------------------------------------
    # Dynamic property – re-evaluated on every coordinator update
    # ------------------------------------------------------------------

    @property
    def current_option(self) -> str | None:
        """
        Return the relay's current mode as a Select option string.

        Decodes the 2-bit raw relay value from the latest coordinator snapshot:

        - ``"auto"`` – bit 1 (manual) is clear; the device schedule controls
          this relay.
        - ``"on"``   – bit 1 set (manual) and bit 0 set (on).
        - ``"off"``  – bit 1 set (manual) and bit 0 clear (off).

        Returns:
            One of ``"auto"``, ``"on"``, ``"off"``, or ``None`` if no data
            has been received yet.
        """
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get_relay_state(self._col)

    # ------------------------------------------------------------------
    # User interaction
    # ------------------------------------------------------------------

    async def async_select_option(self, option: str) -> None:
        """
        Handle the user selecting a new relay mode from the drop-down.

        Validates *option* against ``RELAY_STATES`` then delegates to
        ``ProConIPCoordinator.async_set_relay()``, which encodes the new
        state into the ENA bit pattern and POSTs it to ``/usrcfg.cgi``.

        The coordinator then triggers an immediate refresh so this entity
        (and all others) reflect the change without waiting for the next
        polling interval.

        Args:
            option: The selected option string (``"auto"``, ``"on"``, or
                    ``"off"``).  HA guarantees this is one of the values in
                    ``_attr_options``, but we guard against future API changes.
        """
        if option not in RELAY_STATES:
            # This should never happen because HA validates against _attr_options
            _LOGGER.error(
                "Received invalid relay option %r for col=%d", option, self._col
            )
            return
        await self.coordinator.async_set_relay(self._col, option)
