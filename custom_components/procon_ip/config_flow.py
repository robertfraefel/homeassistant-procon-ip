"""
Config flow for the ProCon.IP integration.

Home Assistant config flows are the UI-driven wizard that guides a user
through adding a new integration instance.  This flow consists of a single
step (``async_step_user``) that:

1. Displays a form asking for host, port, credentials, and polling interval.
2. On submit, attempts a real connection to ``/GetState.csv`` to validate the
   input before persisting anything.
3. On success, creates the ``ConfigEntry`` that triggers ``async_setup_entry``
   in ``__init__.py``.
4. On failure, re-renders the form with a localized error message (keys are
   mapped to strings in ``translations/en.json``).

The ``unique_id`` is set to ``"host:port"`` so that attempting to add the
same device twice is caught and rejected with an ``already_configured`` abort.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_HOST,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_connection(
    hass,
    host: str,
    port: int,
    username: str,
    password: str,
) -> dict[str, str]:
    """
    Attempt a real HTTP connection to verify the user's input.

    Fetches ``/GetState.csv`` using the same HA-managed ``aiohttp`` session
    that the coordinator will use at runtime.  On success it extracts basic
    device info (firmware version) to use as the config entry title.

    Args:
        hass:     The Home Assistant instance (needed for the shared session).
        host:     IP address or hostname entered by the user.
        port:     HTTP port entered by the user.
        username: Basic-auth username (empty string = no auth).
        password: Basic-auth password.

    Returns:
        A dict with at least ``"title"`` (used as the config entry name) and
        ``"firmware"`` (stored for display purposes).

    Raises:
        aiohttp.ClientResponseError: For HTTP 4xx/5xx responses.
            Caller maps 401/403 to ``"invalid_auth"`` and others to
            ``"cannot_connect"``.
        aiohttp.ClientError: For network-level failures (DNS, timeout, …).
            Caller maps these to ``"cannot_connect"``.
    """
    url  = f"http://{host}:{port}/GetState.csv"
    auth = aiohttp.BasicAuth(username, password) if username else None
    session = async_get_clientsession(hass)

    async with session.get(
        url,
        auth=auth,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        resp.raise_for_status()
        text = await resp.text()

    # Extract the firmware version from the SYSINFO row (row 0, index 1)
    first_line = text.strip().splitlines()[0].split(",")
    firmware   = first_line[1] if len(first_line) > 1 else "unknown"

    return {"title": f"ProCon.IP ({host})", "firmware": firmware}


class ProConIPConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle the UI config flow for adding a ProCon.IP device.

    HA instantiates this class when the user starts the "Add Integration"
    wizard and selects *ProCon.IP Pool Controller*.  The flow has a single
    step (``async_step_user``) that collects connection details, validates
    them live against the device, and either creates the entry or re-renders
    the form with an error.

    Class attributes
    ----------------
    VERSION : int
        Schema version of the config entry data dict.  Increment this when
        adding or removing keys so that ``async_migrate_entry`` can upgrade
        old entries automatically.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Handle the (only) step of the config flow.

        On first call ``user_input`` is ``None``, so we render the empty form.
        After the user submits, ``user_input`` contains their values and we
        validate the connection.

        Args:
            user_input: Dict of field values submitted by the user, or
                        ``None`` on the initial render.

        Returns:
            A ``FlowResult`` that is either:

            - ``async_show_form`` to (re-)render the input form.
            - ``async_create_entry`` to finish setup and hand off to
              ``async_setup_entry`` in ``__init__.py``.
            - ``async_abort`` if the device is already configured (handled
              internally by ``_abort_if_unique_id_configured``).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the submitted values by making a real request
            try:
                info = await _validate_connection(
                    self.hass,
                    user_input[CONF_HOST],
                    user_input.get(CONF_PORT, DEFAULT_PORT),
                    user_input.get(CONF_USERNAME, ""),
                    user_input.get(CONF_PASSWORD, ""),
                )
            except aiohttp.ClientResponseError as err:
                # HTTP-level errors: distinguish auth failures from other errors
                if err.status in (401, 403):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError):
                # Network-level errors: DNS failure, connection refused, timeout
                errors["base"] = "cannot_connect"
            except Exception:
                # Catch-all for unexpected failures (e.g. malformed CSV)
                _LOGGER.exception("Unexpected error during ProCon.IP config flow")
                errors["base"] = "unknown"
            else:
                # Validation succeeded – set a unique ID to prevent duplicates
                unique_id = (
                    f"{user_input[CONF_HOST]}:"
                    f"{user_input.get(CONF_PORT, DEFAULT_PORT)}"
                )
                await self.async_set_unique_id(unique_id)
                # Abort with "already_configured" if this device is known
                self._abort_if_unique_id_configured()

                # Persist the config entry and start the integration
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        # Render the form (first call or after validation errors)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    # Host is required; all others are optional with defaults
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                    vol.Optional(
                        CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                    ): int,
                }
            ),
            errors=errors,
        )
