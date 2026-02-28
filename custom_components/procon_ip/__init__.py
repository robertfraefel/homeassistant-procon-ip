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
7. On the first successful setup the Pool dashboard is generated from the
   live device data (so relay entities match the physical wiring) and
   registered with Lovelace so it appears automatically in the sidebar.

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
from homeassistant.util import slugify as ha_slugify

from .const import (
    ALL_RELAY_COLS,
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

# Slug that HA derives from the hardcoded device name "ProCon.IP Pool Controller".
# Entity IDs follow: {domain}.{_DEVICE_SLUG}_{entity_name_slug}
_DEVICE_SLUG = ha_slugify("ProCon.IP Pool Controller")


# ---------------------------------------------------------------------------
# Dashboard YAML generation
# ---------------------------------------------------------------------------

def _get_relay_icon(name: str) -> str:
    """Return a suitable MDI icon for a relay based on its CSV label.

    Args:
        name: The relay's label as read from the ProCon.IP CSV.

    Returns:
        An MDI icon string (e.g. ``"mdi:pump"``).
    """
    n = name.lower()
    if any(w in n for w in ("pump", "pumpe")):
        return "mdi:pump"
    if any(w in n for w in ("light", "licht", "lampe", "lamp")):
        return "mdi:lightbulb"
    if any(w in n for w in ("heat", "heiz")):
        return "mdi:radiator"
    if any(w in n for w in ("valve", "ventil")):
        return "mdi:valve"
    return "mdi:electric-switch"


def _generate_dashboard_yaml(coordinator: ProConIPCoordinator) -> str:
    """Generate the complete Lovelace dashboard YAML with dynamic relay entities.

    The relay section of the Controls view (and the filter-pump quick-view in
    the Overview) is built from the coordinator's live device snapshot so that
    only the relays that are actually wired on the user's ProCon.IP appear in
    the dashboard.  Unconnected relay slots (labelled ``"n.a."`` in the CSV)
    are automatically skipped.

    Args:
        coordinator: The ``ProConIPCoordinator`` that has already completed its
            first data refresh, so ``coordinator.data`` is populated.

    Returns:
        A YAML string ready to be written to a file and served as a Lovelace
        dashboard configuration.
    """
    # Collect every active relay: (entity_id, display_name, icon)
    active_relays: list[tuple[str, str, str]] = []
    if coordinator.data:
        for col in ALL_RELAY_COLS:
            if coordinator.data.is_active(col):
                name = coordinator.data.names[col].strip()
                slug = ha_slugify(name)
                entity_id = f"select.{_DEVICE_SLUG}_{slug}"
                icon = _get_relay_icon(name)
                active_relays.append((entity_id, name, icon))

    # ── Overview: first active relay shown in the filter-pump card ────────
    if active_relays:
        pump_entity, pump_name, pump_icon = active_relays[0]
        overview_pump_card = (
            "\n"
            "      # ── Filter pump & diagnostics ────────────────────────────────────────\n"
            "      - type: horizontal-stack\n"
            "        cards:\n"
            "          - type: entities\n"
            "            title: Filter Pump\n"
            "            icon: mdi:pump\n"
            "            entities:\n"
            f"              - entity: {pump_entity}\n"
            f"                name: {pump_name}\n"
            f"                icon: {pump_icon}\n"
            f"          - type: entity\n"
            f"            entity: sensor.{_DEVICE_SLUG}_kesseldruck\n"
            f"            name: Filter Pressure\n"
            f"            icon: mdi:gauge\n"
            f"          - type: entity\n"
            f"            entity: sensor.{_DEVICE_SLUG}_durchfluss\n"
            f"            name: Flow Rate\n"
            f"            icon: mdi:water-pump\n"
        )
    else:
        overview_pump_card = ""

    # ── Controls: all active relays ───────────────────────────────────────
    if active_relays:
        relay_lines = []
        for entity_id, name, icon in active_relays:
            relay_lines.append(f"          - entity: {entity_id}")
            relay_lines.append(f"            name: {name}")
            relay_lines.append(f"            icon: {icon}")
        relay_block = "\n".join(relay_lines)
    else:
        relay_block = "          # No active relays found on this ProCon.IP"

    d = _DEVICE_SLUG  # short alias for readability in the f-string below

    return (
        f"##############################################################################\n"
        f"#  ProCon.IP Pool Controller – Home Assistant Lovelace Dashboard\n"
        f"#  (auto-generated by the integration – relay entities match your device)\n"
        f"##############################################################################\n"
        f"\n"
        f"title: Pool\n"
        f"views:\n"
        f"\n"
        f"  ##########################################################################\n"
        f"  #  VIEW 1 – OVERVIEW\n"
        f"  ##########################################################################\n"
        f"  - title: Overview\n"
        f"    path: pool-overview\n"
        f"    icon: mdi:pool\n"
        f"    cards:\n"
        f"\n"
        f"      # ── Water quality gauges ─────────────────────────────────────────────\n"
        f"      - type: horizontal-stack\n"
        f"        cards:\n"
        f"\n"
        f"          - type: gauge\n"
        f"            entity: sensor.{d}_pool\n"
        f"            name: Water Temperature\n"
        f"            unit: \u00b0C\n"
        f"            min: 10\n"
        f"            max: 40\n"
        f"            needle: true\n"
        f"            severity:\n"
        f"              green: 10\n"
        f"              yellow: 28\n"
        f"              red: 34\n"
        f"\n"
        f"          - type: gauge\n"
        f"            entity: sensor.{d}_ph\n"
        f"            name: pH Value\n"
        f"            unit: pH\n"
        f"            min: 6.8\n"
        f"            max: 8.2\n"
        f"            needle: true\n"
        f"            severity:\n"
        f"              red: 6.8\n"
        f"              yellow: 7.0\n"
        f"              green: 7.2\n"
        f"\n"
        f"          - type: gauge\n"
        f"            entity: sensor.{d}_redox\n"
        f"            name: Redox (ORP)\n"
        f"            unit: mV\n"
        f"            min: 0\n"
        f"            max: 900\n"
        f"            needle: true\n"
        f"            severity:\n"
        f"              red: 0\n"
        f"              yellow: 650\n"
        f"              green: 750\n"
        f"\n"
        f"      # ── Quick-glance status ───────────────────────────────────────────────\n"
        f"      - type: glance\n"
        f"        title: Current Status\n"
        f"        show_state: true\n"
        f"        show_name: true\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_pool\n"
        f"            name: Water Temp\n"
        f"            icon: mdi:thermometer-water\n"
        f"          - entity: sensor.{d}_ph\n"
        f"            name: pH\n"
        f"            icon: mdi:ph\n"
        f"          - entity: sensor.{d}_redox\n"
        f"            name: Redox\n"
        f"            icon: mdi:flash\n"
        f"          - entity: sensor.{d}_durchfluss\n"
        f"            name: Flow\n"
        f"            icon: mdi:water-pump\n"
        f"          - entity: sensor.{d}_kesseldruck\n"
        f"            name: Pressure\n"
        f"            icon: mdi:gauge\n"
        f"\n"
        f"      # ── Temperature overview ─────────────────────────────────────────────\n"
        f"      - type: entities\n"
        f"        title: Temperatures\n"
        f"        icon: mdi:thermometer\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_pool\n"
        f"            name: Pool water\n"
        f"            icon: mdi:pool-thermometer\n"
        f"          - entity: sensor.{d}_absorber\n"
        f"            name: Solar absorber\n"
        f"            icon: mdi:solar-panel\n"
        f"          - entity: sensor.{d}_rucklauf\n"
        f"            name: Return line\n"
        f"            icon: mdi:pipe\n"
        f"          - entity: sensor.{d}_aussen\n"
        f"            name: Outdoor\n"
        f"            icon: mdi:weather-sunny\n"
        f"{overview_pump_card}"
        f"\n"
        f"      # ── Canister fill levels ─────────────────────────────────────────────\n"
        f"      - type: horizontal-stack\n"
        f"        cards:\n"
        f"          - type: gauge\n"
        f"            entity: sensor.{d}_cl_rest\n"
        f"            name: Chlorine\n"
        f"            unit: \"%\"\n"
        f"            min: 0\n"
        f"            max: 100\n"
        f"            needle: false\n"
        f"            severity:\n"
        f"              red: 0\n"
        f"              yellow: 20\n"
        f"              green: 40\n"
        f"          - type: gauge\n"
        f"            entity: sensor.{d}_ph_rest\n"
        f"            name: pH\u2212\n"
        f"            unit: \"%\"\n"
        f"            min: 0\n"
        f"            max: 100\n"
        f"            needle: false\n"
        f"            severity:\n"
        f"              red: 0\n"
        f"              yellow: 20\n"
        f"              green: 40\n"
        f"\n"
        f"      # ── Chemical consumption ─────────────────────────────────────────────\n"
        f"      - type: entities\n"
        f"        title: Chemical Consumption\n"
        f"        icon: mdi:flask\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_cl_consumption\n"
        f"            name: Chlorine used\n"
        f"            icon: mdi:flask\n"
        f"          - entity: sensor.{d}_ph_consumption\n"
        f"            name: pH\u2212 used\n"
        f"            icon: mdi:flask-outline\n"
        f"\n"
        f"      # ── Digital inputs ────────────────────────────────────────────────────\n"
        f"      - type: entities\n"
        f"        title: Digital Inputs\n"
        f"        icon: mdi:electric-switch-closed\n"
        f"        entities:\n"
        f"          - entity: binary_sensor.{d}_poolabdeckung\n"
        f"            name: Pool Cover\n"
        f"            icon: mdi:shield-sun\n"
        f"          - entity: binary_sensor.{d}_taster2\n"
        f"            name: Button 2\n"
        f"            icon: mdi:gesture-tap-button\n"
        f"          - entity: binary_sensor.{d}_taster3\n"
        f"            name: Button 3\n"
        f"            icon: mdi:gesture-tap-button\n"
        f"\n"
        f"\n"
        f"  ##########################################################################\n"
        f"  #  VIEW 2 – HISTORY / CHARTS\n"
        f"  ##########################################################################\n"
        f"  - title: History\n"
        f"    path: pool-history\n"
        f"    icon: mdi:chart-line\n"
        f"    cards:\n"
        f"\n"
        f"      - type: history-graph\n"
        f"        title: Temperatures (48 h)\n"
        f"        hours_to_show: 48\n"
        f"        refresh_interval: 60\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_pool\n"
        f"            name: Pool water\n"
        f"          - entity: sensor.{d}_absorber\n"
        f"            name: Absorber\n"
        f"          - entity: sensor.{d}_rucklauf\n"
        f"            name: Return line\n"
        f"          - entity: sensor.{d}_aussen\n"
        f"            name: Outdoor\n"
        f"\n"
        f"      - type: history-graph\n"
        f"        title: pH (7 days)\n"
        f"        hours_to_show: 168\n"
        f"        refresh_interval: 300\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_ph\n"
        f"            name: pH\n"
        f"\n"
        f"      - type: history-graph\n"
        f"        title: Redox / ORP (7 days)\n"
        f"        hours_to_show: 168\n"
        f"        refresh_interval: 300\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_redox\n"
        f"            name: Redox\n"
        f"\n"
        f"      - type: history-graph\n"
        f"        title: Flow & Pressure (48 h)\n"
        f"        hours_to_show: 48\n"
        f"        refresh_interval: 60\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_durchfluss\n"
        f"            name: Flow (L/h)\n"
        f"          - entity: sensor.{d}_kesseldruck\n"
        f"            name: Pressure (bar)\n"
        f"\n"
        f"      - type: history-graph\n"
        f"        title: Canister Fill Levels (30 days)\n"
        f"        hours_to_show: 720\n"
        f"        refresh_interval: 3600\n"
        f"        entities:\n"
        f"          - entity: sensor.{d}_cl_rest\n"
        f"            name: Chlorine %\n"
        f"          - entity: sensor.{d}_ph_rest\n"
        f"            name: pH\u2212 %\n"
        f"\n"
        f"\n"
        f"  ##########################################################################\n"
        f"  #  VIEW 3 – RELAY CONTROL\n"
        f"  ##########################################################################\n"
        f"  - title: Controls\n"
        f"    path: pool-controls\n"
        f"    icon: mdi:toggle-switch\n"
        f"    cards:\n"
        f"\n"
        f"      - type: markdown\n"
        f"        content: >\n"
        f"          ## Relay Control\n"
        f"\n"
        f"          Each relay supports three modes:\n"
        f"          **auto** \u2013 ProCon.IP schedule controls this relay |\n"
        f"          **on** \u2013 force permanently on |\n"
        f"          **off** \u2013 force permanently off\n"
        f"\n"
        f"      - type: entities\n"
        f"        title: Relays\n"
        f"        icon: mdi:electric-switch\n"
        f"        entities:\n"
        f"{relay_block}\n"
    )


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------

async def _async_register_dashboard(
    hass: HomeAssistant,
    coordinator: ProConIPCoordinator,
) -> None:
    """Register the Pool dashboard with Lovelace.

    Generates a fresh YAML file from the coordinator's live device snapshot
    so that the relay entities in the dashboard exactly match what is
    physically wired on the user's ProCon.IP.  The generated file is written
    to the HA config directory (``procon_ip_pool_dashboard.yaml``) and served
    via a ``LovelaceYAML`` config object.

    Two registration steps are required:

    1. Add a ``LovelaceYAML`` instance to ``hass.data["lovelace"].dashboards``
       so the YAML content is served when the user navigates to the dashboard.
    2. Call ``frontend.async_register_built_in_panel`` with
       ``component_name="lovelace"`` to create the sidebar entry.

    Args:
        hass:        The Home Assistant instance.
        coordinator: The coordinator whose data is used to discover active
                     relay channels for the dashboard.
    """
    try:
        from homeassistant.components.frontend import async_register_built_in_panel
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

        # Generate YAML with the actual relay entities from the device
        yaml_content = _generate_dashboard_yaml(coordinator)
        generated_path = hass.config.path("procon_ip_pool_dashboard.yaml")

        try:
            await hass.async_add_executor_job(
                Path(generated_path).write_text, yaml_content, "utf-8"
            )
            _LOGGER.debug(
                "ProCon.IP: wrote generated dashboard to %s", generated_path
            )
        except OSError as err:
            _LOGGER.error(
                "ProCon.IP: could not write dashboard YAML to %s (%s) – "
                "dashboard registration skipped.",
                generated_path,
                err,
            )
            return

        config = {
            "mode": "yaml",
            "filename": generated_path,
            "title": "Pool",
            "icon": "mdi:pool",
            "show_in_sidebar": True,
            "require_admin": False,
            "url_path": _DASHBOARD_URL,
        }

        # Step 1 – store the dashboard so HA can serve its YAML content.
        dashboards[_DASHBOARD_URL] = LovelaceYAML(hass, _DASHBOARD_URL, config)

        # Step 2 – register the frontend panel that creates the sidebar entry.
        async_register_built_in_panel(
            hass,
            "lovelace",
            sidebar_title="Pool",
            sidebar_icon="mdi:pool",
            frontend_url_path=_DASHBOARD_URL,
            require_admin=False,
            config={"mode": "yaml"},
            update=False,
        )

        relay_count = sum(
            1 for col in ALL_RELAY_COLS
            if coordinator.data and coordinator.data.is_active(col)
        )
        _LOGGER.info(
            "ProCon.IP: Pool dashboard registered at /%s (%d active relay(s))",
            _DASHBOARD_URL,
            relay_count,
        )

    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.warning(
            "ProCon.IP: could not auto-register the Pool dashboard (%s). "
            "Add it manually via Settings → Dashboards.",
            err,
            exc_info=True,
        )


def _unregister_dashboard(hass: HomeAssistant) -> None:
    """Remove the Pool dashboard from Lovelace when the last entry is unloaded.

    Silently ignores any errors so that unloading the integration always
    succeeds even if the dashboard was never registered (e.g. because
    Lovelace was unavailable during setup).

    Args:
        hass: The Home Assistant instance.
    """
    try:
        from homeassistant.components.frontend import async_remove_panel
        from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN

        ll = hass.data.get(LOVELACE_DOMAIN)
        dashboards = getattr(ll, "dashboards", None)
        if dashboards:
            dashboards.pop(_DASHBOARD_URL, None)

        async_remove_panel(hass, _DASHBOARD_URL)
        _LOGGER.debug("ProCon.IP: Pool dashboard removed from sidebar")

    except Exception:  # pylint: disable=broad-except
        pass


# ---------------------------------------------------------------------------
# Config-entry lifecycle
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the ProCon.IP integration from a config entry.

    Creates a ``ProConIPCoordinator``, performs the first data fetch, stores
    the coordinator in ``hass.data``, forwards platform setup, and registers
    the built-in Pool dashboard in the Lovelace sidebar on the first entry.
    The dashboard is generated dynamically so relay entities reflect the
    physical wiring of the user's device.

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
            await _async_register_dashboard(hass, coordinator)
        else:
            # HA is still starting up; defer until everything is initialised so
            # Lovelace has had a chance to finish its own setup.
            @callback
            def _on_ha_start(_event=None) -> None:
                hass.async_create_task(_async_register_dashboard(hass, coordinator))

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_start)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a ProCon.IP config entry.

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
