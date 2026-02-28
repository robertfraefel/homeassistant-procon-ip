"""
ProCon.IP Pool Controller – Home Assistant integration entry point.

This module is loaded by Home Assistant when the integration is set up for the
first time (``async_setup_entry``) or removed (``async_unload_entry``).  It
is intentionally thin: all business logic lives in ``coordinator.py`` and the
platform modules (``sensor.py``, ``select.py``, ``binary_sensor.py``).

Lifecycle
---------
1. The user opens Settings → Integrations → Add Integration and selects
   *ProCon.IP Pool Controller*.
2. ``config_flow.py`` collects host/port/credentials and validates the
   connection, then creates a ``ConfigEntry`` in ``entry.data``.
3. HA calls ``async_setup_entry`` with the new entry.
4. A ``ProConIPCoordinator`` is instantiated and does its first poll
   (``async_config_entry_first_refresh``).  If that fails, setup is aborted
   and the user sees an error in the UI.
5. The coordinator is stored in ``hass.data[DOMAIN][entry.entry_id]`` so
   every platform module can retrieve it.
6. ``async_forward_entry_setups`` calls each platform's
   ``async_setup_entry`` in turn (sensor → select → binary_sensor).

When the user removes the integration:
7. ``async_unload_entry`` calls ``async_unload_platforms`` which tears down
   every entity, then removes the coordinator from ``hass.data``.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ProConIPCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up the ProCon.IP integration from a config entry.

    Creates a ``ProConIPCoordinator``, performs the first data fetch, stores
    the coordinator in ``hass.data``, and forwards platform setup.

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry created by ``config_flow.py``.

    Returns:
        ``True`` on success.  Returning ``False`` or raising
        ``ConfigEntryNotReady`` would mark the entry as failed and schedule
        a retry.
    """
    # Build the coordinator from config entry data, falling back to defaults
    # for fields that may be absent in entries created by older versions of
    # this integration.
    coordinator = ProConIPCoordinator(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        username=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
        password=entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
        update_interval=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )

    # Perform the first poll before registering entities.  If this raises
    # UpdateFailed, HA converts it to ConfigEntryNotReady and will retry
    # setup automatically with exponential back-off.
    await coordinator.async_config_entry_first_refresh()

    # Store the coordinator so every platform module can access it via:
    #   coordinator = hass.data[DOMAIN][entry.entry_id]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Delegate entity creation to each platform module in PLATFORMS order
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a ProCon.IP config entry.

    Tears down all platform entities and removes the coordinator from
    ``hass.data``.  Called when the user deletes the integration or when HA
    reloads it (e.g. after an options change).

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry being removed.

    Returns:
        ``True`` if all platforms unloaded successfully; ``False`` otherwise
        (which tells HA that the unload failed and the entry stays loaded).
    """
    # Unload every platform; this destroys all entities and their subscriptions
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Only remove the coordinator once all entities have been torn down
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
