"""Constants for ProCon.IP integration."""

DOMAIN = "procon_ip"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_HOST = "192.168.3.17"
DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_UPDATE_INTERVAL = 30  # seconds

PLATFORMS = ["sensor", "select", "binary_sensor"]

# Column ranges for each data category (0-indexed)
COL_RANGE_TIME = range(0, 1)
COL_RANGE_ANALOG = range(1, 6)
COL_RANGE_ELECTRODES = range(6, 8)
COL_RANGE_TEMPERATURES = range(8, 16)
COL_RANGE_RELAYS = range(16, 24)
COL_RANGE_DIGITAL_INPUT = range(24, 28)
COL_RANGE_EXTERNAL_RELAYS = range(28, 36)
COL_RANGE_CANISTER = range(36, 39)
COL_RANGE_CANISTER_CONSUMPTION = range(39, 42)

# All relay column ranges combined
ALL_RELAY_COLS = list(COL_RANGE_RELAYS) + list(COL_RANGE_EXTERNAL_RELAYS)

# Relay raw value bit masks
RELAY_BIT_ON = 1      # bit 0: 0 = off,    1 = on
RELAY_BIT_MANUAL = 2  # bit 1: 0 = auto,   1 = manual

# Relay select-entity option strings
RELAY_STATE_AUTO = "auto"
RELAY_STATE_ON = "on"
RELAY_STATE_OFF = "off"
RELAY_STATES = [RELAY_STATE_AUTO, RELAY_STATE_ON, RELAY_STATE_OFF]

# CSV unit string → HA unit string
UNIT_MAP: dict[str, str | None] = {
    "C":    "°C",
    "Bar":  "bar",
    "mV":   "mV",
    "pH":   "pH",
    "%":    "%",
    "ml":   "mL",
    "l/h":  "L/h",
    "h":    "h",
    "--":   None,
    "":     None,
}

# HA unit → suggested display precision
UNIT_PRECISION: dict[str, int] = {
    "°C":  1,
    "bar": 3,
    "mV":  0,
    "pH":  2,
    "%":   0,
    "mL":  0,
    "L/h": 0,
    "h":   0,
}
