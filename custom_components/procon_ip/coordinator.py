"""DataUpdateCoordinator for ProCon.IP."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    ALL_RELAY_COLS,
    RELAY_BIT_MANUAL,
    RELAY_BIT_ON,
    RELAY_STATE_AUTO,
    RELAY_STATE_ON,
    RELAY_STATE_OFF,
)

_LOGGER = logging.getLogger(__name__)


class ProConIPData:
    """Holds parsed data from a single GetState.csv response."""

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
        self.names = names
        self.units = units
        self.offsets = offsets
        self.factors = factors
        self.raws = raws
        self.values = values

    @property
    def firmware(self) -> str:
        return self.sysinfo[1] if len(self.sysinfo) > 1 else "unknown"

    @property
    def device_id(self) -> str:
        return self.sysinfo[2] if len(self.sysinfo) > 2 else ""

    def is_active(self, col: int) -> bool:
        """Return True if the column has a real (non-n.a.) label."""
        if col >= len(self.names):
            return False
        name = self.names[col].strip().lower()
        return name not in ("n.a.", "")

    def get_relay_state(self, col: int) -> str:
        """Return relay state string: 'auto', 'on', or 'off'."""
        raw = self.raws[col] if col < len(self.raws) else 0
        is_manual = bool(raw & RELAY_BIT_MANUAL)
        is_on = bool(raw & RELAY_BIT_ON)
        if not is_manual:
            return RELAY_STATE_AUTO
        return RELAY_STATE_ON if is_on else RELAY_STATE_OFF

    def compute_ena_bits(self) -> tuple[int, int]:
        """
        Compute ENA bit patterns for /usrcfg.cgi from current relay states.

        bit_states_0: manual-mode bits (1 = manual, 0 = auto); initialize all to 1.
        bit_states_1: on-state bits   (1 = on,     0 = off);  initialize all to 0.
        """
        has_external = any(self.is_active(c) for c in range(28, 36))
        bit_states_0 = 65535 if has_external else 255
        bit_states_1 = 0

        for i, col in enumerate(ALL_RELAY_COLS):
            if col >= len(self.raws):
                break
            bit_mask = 1 << i
            raw = self.raws[col]
            is_manual = bool(raw & RELAY_BIT_MANUAL)
            is_on = bool(raw & RELAY_BIT_ON)

            if not is_manual:          # auto → clear manual bit
                bit_states_0 &= ~bit_mask
            if is_on:                  # on → set on bit
                bit_states_1 |= bit_mask

        return bit_states_0, bit_states_1


def _parse_csv(text: str) -> ProConIPData:
    """Parse the raw CSV text from /GetState.csv."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 6:
        raise ValueError(f"Expected ≥6 rows in GetState.csv, got {len(lines)}")

    sysinfo = lines[0].split(",")
    names   = lines[1].split(",")
    units   = lines[2].split(",")

    offsets = [float(v) for v in lines[3].split(",")]
    factors = [float(v) for v in lines[4].split(",")]
    # Raw values are integers stored as floats in the CSV
    raws    = [int(float(v)) for v in lines[5].split(",")]

    # Actual display value = offset + factor * raw
    values = [
        offsets[i] + factors[i] * raws[i]
        for i in range(len(raws))
    ]

    return ProConIPData(sysinfo, names, units, offsets, factors, raws, values)


class ProConIPCoordinator(DataUpdateCoordinator[ProConIPData]):
    """Polls /GetState.csv and exposes relay-control helpers."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
        update_interval: int,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._base_url = f"http://{host}:{port}"

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    @property
    def device_info(self) -> DeviceInfo:
        firmware = self.data.firmware if self.data else "unknown"
        device_id = self.data.device_id if self.data else f"{self.host}:{self.port}"
        return DeviceInfo(
            identifiers={(DOMAIN, device_id or f"{self.host}:{self.port}")},
            name="ProCon.IP Pool Controller",
            manufacturer="ProCon.IP",
            model="Pool Controller",
            sw_version=firmware,
            configuration_url=self._base_url,
        )

    def _build_auth(self) -> aiohttp.BasicAuth | None:
        if self.username:
            return aiohttp.BasicAuth(self.username, self.password)
        return None

    async def _async_update_data(self) -> ProConIPData:
        """Fetch and parse /GetState.csv."""
        url = f"{self._base_url}/GetState.csv"
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
            raise UpdateFailed(f"HTTP error {err.status} from ProCon.IP") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error: {err}") from err

        try:
            return _parse_csv(text)
        except (ValueError, IndexError) as err:
            raise UpdateFailed(f"CSV parse error: {err}") from err

    async def async_set_relay(self, relay_col: int, state: str) -> None:
        """
        Set one relay to 'auto', 'on', or 'off' via /usrcfg.cgi.

        The ENA parameter encodes *all* relay states at once as two decimal
        integers (manual-bits, on-bits).  We read the current state, modify
        only the target relay's bits, then POST the full pattern.
        """
        if self.data is None:
            _LOGGER.warning("Cannot set relay: no data available yet")
            return

        if relay_col not in ALL_RELAY_COLS:
            _LOGGER.error("relay_col %d is not a valid relay column", relay_col)
            return

        relay_index = ALL_RELAY_COLS.index(relay_col)
        bit_mask = 1 << relay_index

        bit_states_0, bit_states_1 = self.data.compute_ena_bits()

        if state == RELAY_STATE_AUTO:
            bit_states_0 &= ~bit_mask   # clear manual bit
            bit_states_1 &= ~bit_mask   # clear on bit
        elif state == RELAY_STATE_ON:
            bit_states_0 |= bit_mask    # set manual bit
            bit_states_1 |= bit_mask    # set on bit
        elif state == RELAY_STATE_OFF:
            bit_states_0 |= bit_mask    # set manual bit
            bit_states_1 &= ~bit_mask   # clear on bit
        else:
            _LOGGER.error("Unknown relay state: %s", state)
            return

        url = f"{self._base_url}/usrcfg.cgi"
        payload = f"ENA={bit_states_0},{bit_states_1}&MANUAL=1"
        session = async_get_clientsession(self.hass)

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
            _LOGGER.error("Failed to set relay %d to %s: %s", relay_col, state, err)
            return

        await self.async_request_refresh()
