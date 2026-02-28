"""
DataUpdateCoordinator and data-model classes for the ProCon.IP integration.

Architecture overview
---------------------
This module is the heart of the integration and is responsible for two things:

1. **Fetching data** – ``ProConIPCoordinator`` subclasses HA's
   ``DataUpdateCoordinator`` and polls ``/GetState.csv`` at a configurable
   interval.  All sensor, select, and binary-sensor entities subscribe to the
   coordinator via ``CoordinatorEntity``; when fresh data arrives every entity
   updates itself without making its own network call.

2. **Controlling relays** – ``ProConIPCoordinator.async_set_relay()`` encodes
   a relay state change into the binary ``ENA`` parameter expected by
   ``/usrcfg.cgi`` and POSTs it to the device.

Data flow
---------
::

    Device /GetState.csv
          │
          ▼
    _parse_csv()           parses the 6-row CSV into typed lists
          │
          ▼
    ProConIPData           immutable snapshot (one per poll cycle)
          │
          ▼
    ProConIPCoordinator    distributes the snapshot to all subscribed entities

    ─────────────────────────────────────────────

    User changes Select entity
          │
          ▼
    async_set_relay()      reads current ProConIPData, flips one relay's bits
          │
          ▼
    POST /usrcfg.cgi       sends full ENA bit pattern to the device
          │
          ▼
    async_request_refresh() triggers an immediate re-poll so entities update

Relay ENA protocol
------------------
The ``/usrcfg.cgi`` endpoint does not accept per-relay commands.  Instead it
expects the complete state of every relay encoded as two decimal integers in
the POST body::

    ENA=<manual_bits>,<on_bits>&MANUAL=1

``manual_bits``
    Bit i set means relay i is in **manual** mode (not auto-scheduled).
    Initialised to 255 (or 65535 with external relays) so all relays are
    "manual" by default; auto-mode relays then clear their bit.

``on_bits``
    Bit i set means relay i is currently **on**.
    Initialised to 0 (all off); on-relays set their bit.

Bit index i maps to column index ``ALL_RELAY_COLS[i]``
(bit 0 → col 16, bit 7 → col 23, bit 8 → col 28, …).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ALL_RELAY_COLS,
    DOMAIN,
    RELAY_BIT_MANUAL,
    RELAY_BIT_ON,
    RELAY_STATE_AUTO,
    RELAY_STATE_OFF,
    RELAY_STATE_ON,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class ProConIPData:
    """
    Immutable snapshot of one ``/GetState.csv`` response.

    The constructor accepts the six parsed rows from the CSV and pre-computes
    the ``values`` list so entity code never has to redo the arithmetic
    (``value = offset + factor × raw``).

    Attributes
    ----------
    sysinfo : list[str]
        Tokens from CSV row 0, e.g. ``["SYSINFO", "1.7.6", "30217075", …]``.
        Index 1 is the firmware version; index 2 is the device identifier.
    names : list[str]
        Human-readable column labels from row 1, e.g. ``"Pool"``, ``"pH"``,
        ``"n.a."`` (for unconnected channels).
    units : list[str]
        Raw unit strings from row 2, e.g. ``"C"``, ``"pH"``, ``"--"``.
    offsets : list[float]
        Per-column calibration offsets from row 3.
    factors : list[float]
        Per-column scale factors from row 4.
    raws : list[int]
        Raw integer readings from the hardware, transmitted in row 5.
    values : list[float]
        Pre-computed display values: ``offsets[i] + factors[i] * raws[i]``.
    """

    def __init__(
        self,
        sysinfo: list[str],
        names: list[str],
        units: list[str],
        offsets: list[float],
        factors: list[float],
        raws: list[int],
        values: list[float],
    ) -> None:
        self.sysinfo = sysinfo
        self.names   = names
        self.units   = units
        self.offsets = offsets
        self.factors = factors
        self.raws    = raws
        self.values  = values

    # ------------------------------------------------------------------
    # Convenience properties derived from the SYSINFO row
    # ------------------------------------------------------------------

    @property
    def firmware(self) -> str:
        """
        Firmware version string extracted from SYSINFO index 1.

        Example return value: ``"1.7.6"``.
        Falls back to ``"unknown"`` if the SYSINFO row is unexpectedly short.
        """
        return self.sysinfo[1] if len(self.sysinfo) > 1 else "unknown"

    @property
    def device_id(self) -> str:
        """
        Unique device identifier from SYSINFO index 2 (e.g. ``"30217075"``).

        Used as the stable ``identifiers`` key in ``DeviceInfo`` so the HA
        device entry survives IP-address changes.  Falls back to an empty
        string if the field is absent.
        """
        return self.sysinfo[2] if len(self.sysinfo) > 2 else ""

    # ------------------------------------------------------------------
    # Column helpers
    # ------------------------------------------------------------------

    def is_active(self, col: int) -> bool:
        """
        Return ``True`` when column *col* has a real, non-placeholder label.

        The device sets the name column to ``"n.a."`` for channels that are
        not physically wired.  Those columns are skipped when creating entities
        so the HA device card only shows meaningful sensors and controls.

        Args:
            col: 0-based CSV column index.

        Returns:
            ``True`` when the label is not ``"n.a."`` and not an empty string;
            ``False`` otherwise (including when *col* is out of range).
        """
        if col >= len(self.names):
            return False
        name = self.names[col].strip().lower()
        return name not in ("n.a.", "")

    def get_relay_state(self, col: int) -> str:
        """
        Decode a relay column's raw value into a human-readable state string.

        Each relay's raw integer is a 2-bit value:

        ====  ======  ========  ================================
        raw   bit 1   bit 0     meaning
              manual  on
        ====  ======  ========  ================================
         0      0       0       auto mode, currently off
         1      0       1       auto mode, currently on
         2      1       0       manual mode, forced off
         3      1       1       manual mode, forced on
        ====  ======  ========  ================================

        Args:
            col: 0-based CSV column index for the relay.

        Returns:
            ``"auto"`` when bit 1 (manual) is clear; otherwise ``"on"`` or
            ``"off"`` depending on bit 0 (on/off).
        """
        raw       = self.raws[col] if col < len(self.raws) else 0
        is_manual = bool(raw & RELAY_BIT_MANUAL)  # bit 1
        is_on     = bool(raw & RELAY_BIT_ON)       # bit 0
        if not is_manual:
            return RELAY_STATE_AUTO
        return RELAY_STATE_ON if is_on else RELAY_STATE_OFF

    def compute_ena_bits(self) -> tuple[int, int]:
        """
        Build the two ENA bit-pattern integers from the current relay states.

        ``/usrcfg.cgi`` requires a full snapshot of all relays at once.
        Bit i in each integer corresponds to relay at ``ALL_RELAY_COLS[i]``.

        Algorithm
        ---------
        1. Initialise ``bit_states_0`` to 255 (or 65535 if external relays are
           active) — all relays treated as "manual" to start.
        2. Initialise ``bit_states_1`` to 0 — all relays off to start.
        3. For each relay in order:

           - If it is in **auto** mode → clear its bit in ``bit_states_0``.
           - If it is currently **on** → set its bit in ``bit_states_1``.

        Returns:
            ``(bit_states_0, bit_states_1)`` where:

            - ``bit_states_0``: manual-mode bits (1 = manual, 0 = auto).
            - ``bit_states_1``: on-state bits (1 = on, 0 = off).
        """
        # Use 16-bit patterns when external relays are wired up
        has_external = any(self.is_active(c) for c in range(28, 36))
        bit_states_0 = 65535 if has_external else 255  # all manual initially
        bit_states_1 = 0                               # all off initially

        for i, col in enumerate(ALL_RELAY_COLS):
            if col >= len(self.raws):
                # CSV is shorter than expected; stop (older firmware may omit
                # trailing columns)
                break

            bit_mask  = 1 << i
            raw       = self.raws[col]
            is_manual = bool(raw & RELAY_BIT_MANUAL)
            is_on     = bool(raw & RELAY_BIT_ON)

            if not is_manual:
                # Auto mode → clear the manual bit so the device keeps
                # controlling this relay via its internal schedule
                bit_states_0 &= ~bit_mask
            if is_on:
                # Relay is currently on → mark it in the on-bits pattern
                bit_states_1 |= bit_mask

        return bit_states_0, bit_states_1


# ---------------------------------------------------------------------------
# CSV parser (module-private)
# ---------------------------------------------------------------------------

def _parse_csv(text: str) -> ProConIPData:
    """
    Parse the raw text from ``GET /GetState.csv`` into a ``ProConIPData``.

    The response has no traditional header row; instead each line has a fixed
    semantic role (see the module docstring).  Blank lines are discarded so
    the parser tolerates trailing newlines sent by some firmware versions.

    Args:
        text: Raw HTTP response body from ``/GetState.csv``.

    Returns:
        A fully populated ``ProConIPData`` instance.

    Raises:
        ValueError: If there are fewer than 6 non-blank lines.
        IndexError: If a row has fewer comma-separated values than expected.
    """
    # Filter out blank lines (some firmware appends a trailing blank line)
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]

    if len(lines) < 6:
        raise ValueError(
            f"Expected ≥6 rows in GetState.csv, got {len(lines)}"
        )

    # Parse each row according to its fixed semantic role
    sysinfo = lines[0].split(",")  # Row 0: SYSINFO (firmware, device ID, …)
    names   = lines[1].split(",")  # Row 1: Column labels (or "n.a.")
    units   = lines[2].split(",")  # Row 2: Unit strings (C, Bar, mV, pH, …)

    offsets = [float(v) for v in lines[3].split(",")]  # Row 3: Calibration offsets
    factors = [float(v) for v in lines[4].split(",")]  # Row 4: Scale factors

    # Row 5: Raw integer readings.  Some firmware versions format these as
    # floats (e.g. "124.0"), so we parse through float before casting to int.
    raws = [int(float(v)) for v in lines[5].split(",")]

    # Pre-compute the actual display value for every column:
    #   displayed value = offset + factor × raw
    values = [
        offsets[i] + factors[i] * raws[i]
        for i in range(len(raws))
    ]

    return ProConIPData(sysinfo, names, units, offsets, factors, raws, values)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class ProConIPCoordinator(DataUpdateCoordinator[ProConIPData]):
    """
    Periodic data fetcher and relay-control gateway for one ProCon.IP device.

    This coordinator is the single network gateway between the integration and
    the physical device.  It is created once per config entry by
    ``async_setup_entry`` in ``__init__.py`` and stored in
    ``hass.data[DOMAIN][entry.entry_id]``.

    Every entity class (sensor, select, binary_sensor) inherits from
    ``CoordinatorEntity[ProConIPCoordinator]`` and receives a push
    notification whenever ``_async_update_data`` publishes a new
    ``ProConIPData`` snapshot.  This means only *one* HTTP request is made per
    polling cycle regardless of how many entities are registered.

    Args:
        hass:            The running Home Assistant instance.
        host:            IP address or hostname of the ProCon.IP device.
        port:            HTTP port (default 80).
        username:        Basic-auth username; pass an empty string to disable
                         authentication.
        password:        Basic-auth password.
        update_interval: Polling period in seconds.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
        update_interval: int,
    ) -> None:
        self.host      = host
        self.port      = port
        self.username  = username
        self.password  = password
        self._base_url = f"http://{host}:{port}"

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    # ------------------------------------------------------------------
    # Device info (shared by all entities belonging to this coordinator)
    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        """
        Return HA ``DeviceInfo`` describing the ProCon.IP unit.

        All entities from this coordinator share the same ``DeviceInfo`` so
        they appear grouped under a single device card in the HA UI.

        The ``identifiers`` tuple uses the device's own SYSINFO ID as the
        stable unique key so the device entry survives IP address changes.
        When that field is absent (very old firmware), ``host:port`` is used
        as a fallback.
        """
        firmware  = self.data.firmware  if self.data else "unknown"
        device_id = self.data.device_id if self.data else ""
        return DeviceInfo(
            identifiers={(DOMAIN, device_id or f"{self.host}:{self.port}")},
            name="ProCon.IP Pool Controller",
            manufacturer="ProCon.IP",
            model="Pool Controller",
            sw_version=firmware,
            # Clicking the device card opens the native ProCon.IP web UI
            configuration_url=self._base_url,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_auth(self) -> aiohttp.BasicAuth | None:
        """
        Build a ``BasicAuth`` object from the stored credentials.

        Returns ``None`` when ``self.username`` is an empty string so that
        ``aiohttp`` omits the ``Authorization`` header entirely (devices that
        do not require authentication reject non-empty auth headers on some
        firmware versions).
        """
        if self.username:
            return aiohttp.BasicAuth(self.username, self.password)
        return None

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ProConIPData:
        """
        Fetch ``/GetState.csv`` and return a fresh ``ProConIPData`` snapshot.

        Called automatically by the base class at every ``update_interval``
        tick and on demand via ``async_request_refresh()``.  Uses the
        HA-managed ``aiohttp`` session (``async_get_clientsession``) rather
        than creating a new session per request, which avoids exhausting
        file-descriptor limits on busy systems.

        Returns:
            A freshly parsed ``ProConIPData``.

        Raises:
            UpdateFailed: Wraps any network error or CSV parse failure.
                The base class catches this, logs the error, and marks all
                subscribed entities as ``unavailable`` once the error count
                exceeds the coordinator's ``error_tolerance``.
        """
        url     = f"{self._base_url}/GetState.csv"
        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                url,
                auth=self._build_auth(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(
                f"HTTP {err.status} error fetching {url}"
            ) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                f"Network error fetching {url}: {err}"
            ) from err

        try:
            return _parse_csv(text)
        except (ValueError, IndexError) as err:
            raise UpdateFailed(f"Failed to parse GetState.csv: {err}") from err

    # ------------------------------------------------------------------
    # Relay control
    # ------------------------------------------------------------------

    async def async_set_relay(self, relay_col: int, state: str) -> None:
        """
        Switch one relay to ``'auto'``, ``'on'``, or ``'off'``.

        The ProCon.IP ``/usrcfg.cgi`` endpoint replaces the state of **all**
        relays in one POST, so a per-relay command must:

        1. Derive the current full-state bit patterns from the latest cached
           ``ProConIPData`` snapshot using ``compute_ena_bits()``.
        2. Modify only the target relay's two bits.
        3. POST the updated ``ENA`` string to the device.
        4. Request an immediate coordinator refresh so entities reflect the
           change without waiting for the next scheduled poll.

        Bit-manipulation rules (mirrors the procon-ip TypeScript library):

        ========  ========================  ======================
        state     ``bit_states_0`` (manual) ``bit_states_1`` (on)
        ========  ========================  ======================
        ``auto``  clear bit i               clear bit i
        ``on``    set   bit i               set   bit i
        ``off``   set   bit i               clear bit i
        ========  ========================  ======================

        Args:
            relay_col: 0-based CSV column index of the relay to change.
                       Must be a member of ``ALL_RELAY_COLS`` (16–23 for
                       internal relays, 28–35 for external relays).
            state:     Desired state string: ``"auto"``, ``"on"``,
                       or ``"off"``.
        """
        if self.data is None:
            _LOGGER.warning(
                "Cannot set relay col=%d: coordinator has no data yet", relay_col
            )
            return

        if relay_col not in ALL_RELAY_COLS:
            _LOGGER.error(
                "relay_col %d is not a valid relay column; "
                "valid columns are %s",
                relay_col,
                ALL_RELAY_COLS,
            )
            return

        # Locate this relay's position in the bit patterns
        relay_index = ALL_RELAY_COLS.index(relay_col)
        bit_mask    = 1 << relay_index  # e.g. relay_index=2 → bit_mask=4

        # Read the current full-state bit patterns from the cached snapshot
        bit_states_0, bit_states_1 = self.data.compute_ena_bits()

        # Flip only the target relay's bits according to the requested state
        if state == RELAY_STATE_AUTO:
            bit_states_0 &= ~bit_mask   # clear manual bit → auto mode
            bit_states_1 &= ~bit_mask   # clear on bit (irrelevant in auto)
        elif state == RELAY_STATE_ON:
            bit_states_0 |= bit_mask    # set manual bit → manual mode
            bit_states_1 |= bit_mask    # set on bit → relay energised
        elif state == RELAY_STATE_OFF:
            bit_states_0 |= bit_mask    # set manual bit → manual mode
            bit_states_1 &= ~bit_mask   # clear on bit → relay de-energised
        else:
            _LOGGER.error("Unknown relay state requested: %r", state)
            return

        # Build the POST body and send it to the device
        url     = f"{self._base_url}/usrcfg.cgi"
        payload = f"ENA={bit_states_0},{bit_states_1}&MANUAL=1"
        session = async_get_clientsession(self.hass)

        _LOGGER.debug(
            "Relay col=%d → %r  (ENA=%d,%d)",
            relay_col, state, bit_states_0, bit_states_1,
        )

        try:
            async with session.post(
                url,
                data=payload,
                auth=self._build_auth(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Failed to set relay col=%d to %r: %s", relay_col, state, err
            )
            return

        # Trigger an immediate poll so all entities reflect the new state
        await self.async_request_refresh()
