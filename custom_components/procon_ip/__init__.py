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
7. On the first successful setup the built-in Pool dashboard is registered
   with Lovelace so it appears automatically in the sidebar.

When the user removes the integration:
8. ``async_unload_entry`` calls ``async_unload_platforms`` which tears down
   every entity, then removes the coordinator from ``hass.data``.
9. When the last ProCon.IP config entry is removed the Pool dashboard is
   also removed from the sidebar.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback

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

# URL path under which the Pool dashboard is registered in Lovelace.
# Users can reach it at  http://<ha-host>/procon-ip-pool
_DASHBOARD_URL = "procon-ip-pool"

# Path to the bundled dashboard YAML (ships inside the integration package
# so it is installed automatically by HACS along with the Python files).
_DASHBOARD_YAML = Path(__file__).parent / "pool_dashboard.yaml"


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

async def _async_register_dashboard(hass: HomeAssistant) -> None:
    """
    Register the built-in Pool dashboard with Lovelace.

    Adds the dashboard at ``/procon-ip-pool`` so it appears in the HA
    sidebar without any manual steps.  The function is a no-op when the
    dashboard is already registered (idempotent – safe on reload).

    Args:
        hass: The Home Assistant instance.
    """
    try:
        from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN
        from homeassistant.components.lovelace.dashboard import LovelaceYAML

        ll = hass.data.get(LOVELACE_DOMAIN)
        # In modern HA, hass.data["lovelace"] is a LovelaceData dataclass;
        # access the dashboards dict via attribute, not subscript.
        dashboards = getattr(ll, "dashboards", None)
        if ll is None or dashboards is None:
            _LOGGER.warning(
                "ProCon.IP: Lovelace is not initialised – Pool dashboard was "
                "not added to the sidebar. Add it manually via "
                "Settings → Dashboards."
            )
            return

        if _DASHBOARD_URL in dashboards:
            return  # already registered (e.g. integration reload)

        if not _DASHBOARD_YAML.exists():
            _LOGGER.error(
                "ProCon.IP: bundled dashboard file not found at %s – "
                "sidebar entry skipped.",
                _DASHBOARD_YAML,
            )
            return

        # url_path is included both as a kwarg and inside the config dict
        # because different HA versions read it from one or the other place.
        config = {
            "mode": "yaml",
            "filename": str(_DASHBOARD_YAML),
            "title": "Pool",
            "icon": "mdi:pool",
            "show_in_sidebar": True,
            "require_admin": False,
            "url_path": _DASHBOARD_URL,
        }
        dashboards[_DASHBOARD_URL] = LovelaceYAML(hass, _DASHBOARD_URL, config)

        # Notify the frontend so the sidebar updates without a browser refresh.
        hass.bus.async_fire(
            "lovelace_updated",
            {"action": "create", "url_path": _DASHBOARD_URL},
        )
        _LOGGER.info("ProCon.IP: Pool dashboard registered at /%s", _DASHBOARD_URL)

    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.warning(
            "ProCon.IP: could not auto-register the Pool dashboard (%s). "
            "Add it manually via Settings → Dashboards.",
            err,
            exc_info=True,
        )


def _unregister_dashboard(hass: HomeAssistant) -> None:
    """
    Remove the Pool dashboard from Lovelace when the last entry is unloaded.

    Silently ignores any errors so that unloading the integration always
    succeeds even if the dashboard was never registered (e.g. because
    Lovelace was unavailable during setup).

    Args:
        hass: The Home Assistant instance.
    """
    try:
        from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN

        ll = hass.data.get(LOVELACE_DOMAIN)
        dashboards = getattr(ll, "dashboards", None)
        if dashboards and _DASHBOARD_URL in dashboards:
            dashboards.pop(_DASHBOARD_URL)
            hass.bus.async_fire(
                "lovelace_updated",
                {"action": "delete", "url_path": _DASHBOARD_URL},
            )
            _LOGGER.debug("Pool dashboard removed from sidebar")

    except Exception:  # pylint: disable=broad-except
        pass


# ---------------------------------------------------------------------------
# Config-entry lifecycle
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up the ProCon.IP integration from a config entry.

    Creates a ``ProConIPCoordinator``, performs the first data fetch, stores
    the coordinator in ``hass.data``, forwards platform setup, and registers
    the built-in Pool dashboard in the Lovelace sidebar on the first entry.

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

    # Register the sidebar dashboard on the first ProCon.IP entry only.
    # Subsequent entries (multiple devices) skip this – one dashboard suffices.
    if len(hass.data[DOMAIN]) == 1:
        if hass.state == CoreState.running:
            # HA is already up (e.g. integration loaded via UI without restart)
            await _async_register_dashboard(hass)
        else:
            # HA is still starting up; defer until everything is initialised so
            # Lovelace has had a chance to finish its own setup.
            @callback
            def _on_ha_start(_event=None) -> None:
                hass.async_create_task(_async_register_dashboard(hass))

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_start)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a ProCon.IP config entry.

    Tears down all platform entities and removes the coordinator from
    ``hass.data``.  When the last ProCon.IP entry is removed the Pool
    dashboard is also removed from the Lovelace sidebar.

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

        # Remove the sidebar dashboard when no ProCon.IP entries remain
        if not hass.data[DOMAIN]:
            _unregister_dashboard(hass)

    return unload_ok
