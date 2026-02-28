"""
Constants for the ProCon.IP Home Assistant integration.

The ProCon.IP pool controller exposes its entire state through a single HTTP
endpoint – ``/GetState.csv`` – which returns a fixed-layout CSV document.
This module centralises every magic number, key string, and lookup table used
across the integration so they can be updated in one place without hunting
through multiple files.

CSV column layout (0-indexed)
------------------------------
The device always returns the same number of columns in the same order.
Columns that are not physically connected are labelled ``n.a.`` in row 1.

 Col 0        Time                 – internal processing time (hours)
 Col 1 –  5  Analog               – general-purpose analog channels
                                      (mV, Bar, °C depending on hardware)
 Col 6 –  7  Electrodes           – Redox (mV) and pH value
 Col 8 – 15  Temperatures         – up to eight temperature probes (°C)
 Col 16 – 23 Internal relays      – eight software-switched relay outputs (--)
 Col 24 – 27 Digital inputs       – flow sensor (l/h) and digital I/O (--)
 Col 28 – 35 External relays      – eight optional external relay outputs (--)
 Col 36 – 38 Canister fill levels – chemical canister levels (%)
 Col 39 – 41 Canister consumption – cumulative chemical usage (mL)

CSV row layout
--------------
 Row 0  SYSINFO   – firmware version, device ID, and system flags
 Row 1  Names     – human-readable label per column (or "n.a." if unused)
 Row 2  Units     – unit string per column (C, Bar, mV, pH, %, ml, l/h, --)
 Row 3  Offsets   – calibration offset to add after scaling
 Row 4  Factors   – scale factor to multiply the raw integer by
 Row 5  Raws      – raw integer readings from the device hardware

Actual displayed value = offset + factor × raw
"""

# ---------------------------------------------------------------------------
# Integration domain – must match the folder name custom_components/<domain>
# ---------------------------------------------------------------------------
DOMAIN = "procon_ip"

# ---------------------------------------------------------------------------
# Config-entry keys  (stored in entry.data by config_flow.py)
# ---------------------------------------------------------------------------
CONF_HOST            = "host"             # IP address or hostname of the device
CONF_PORT            = "port"             # HTTP port (default 80)
CONF_USERNAME        = "username"         # Basic-auth username
CONF_PASSWORD        = "password"         # Basic-auth password
CONF_UPDATE_INTERVAL = "update_interval"  # Polling interval in seconds

# ---------------------------------------------------------------------------
# Defaults used in config_flow.py (shown as pre-filled form values) and in
# __init__.py (used as fallbacks when a key is missing from entry.data).
# ---------------------------------------------------------------------------
DEFAULT_HOST            = "192.168.3.17"
DEFAULT_PORT            = 80
DEFAULT_USERNAME        = "admin"
DEFAULT_PASSWORD        = "admin"
DEFAULT_UPDATE_INTERVAL = 30  # seconds

# ---------------------------------------------------------------------------
# HA platform identifiers – forwarded in sequence by async_setup_entry so
# that each platform module's async_setup_entry is called automatically.
# ---------------------------------------------------------------------------
PLATFORMS = ["sensor", "select", "binary_sensor"]

# ---------------------------------------------------------------------------
# CSV column ranges for each data category
# These are used to know which columns belong to which HA platform and to
# iterate only over the relevant subset when creating entities.
# ---------------------------------------------------------------------------
COL_RANGE_TIME                 = range(0,  1)   # 1  column  – internal timer
COL_RANGE_ANALOG               = range(1,  6)   # 5  columns – raw analog channels
COL_RANGE_ELECTRODES           = range(6,  8)   # 2  columns – Redox + pH
COL_RANGE_TEMPERATURES         = range(8,  16)  # 8  columns – temperature probes
COL_RANGE_RELAYS               = range(16, 24)  # 8  columns – internal relays
COL_RANGE_DIGITAL_INPUT        = range(24, 28)  # 4  columns – digital I/O
COL_RANGE_EXTERNAL_RELAYS      = range(28, 36)  # 8  columns – external relays
COL_RANGE_CANISTER             = range(36, 39)  # 3  columns – fill levels (%)
COL_RANGE_CANISTER_CONSUMPTION = range(39, 42)  # 3  columns – consumption (mL)

# Flat list of every relay column in bit-index order.
# Position i in this list maps to bit i in the ENA parameter sent to
# /usrcfg.cgi:  bit 0 → col 16 (first internal relay),
#               bit 7 → col 23 (last internal relay),
#               bit 8 → col 28 (first external relay), etc.
ALL_RELAY_COLS = list(COL_RANGE_RELAYS) + list(COL_RANGE_EXTERNAL_RELAYS)

# ---------------------------------------------------------------------------
# Relay raw-value bit masks
#
# The device encodes each relay's state as a 2-bit integer:
#
#   bit 0  (RELAY_BIT_ON)     – 0 = currently off,    1 = currently on
#   bit 1  (RELAY_BIT_MANUAL) – 0 = auto / scheduled, 1 = manual override
#
# Combined raw values:
#   0  → auto mode, currently off   (device schedule turned it off)
#   1  → auto mode, currently on    (device schedule turned it on)
#   2  → manual mode, off           (user forced it off)
#   3  → manual mode, on            (user forced it on)
# ---------------------------------------------------------------------------
RELAY_BIT_ON     = 1  # least-significant bit – on/off state
RELAY_BIT_MANUAL = 2  # second bit            – manual/auto mode

# ---------------------------------------------------------------------------
# Relay state option strings – these are the values presented in the
# Home Assistant Select entity drop-down for each relay.
# ---------------------------------------------------------------------------
RELAY_STATE_AUTO = "auto"   # return control to the ProCon.IP timer/schedule
RELAY_STATE_ON   = "on"     # force relay permanently on  (manual mode)
RELAY_STATE_OFF  = "off"    # force relay permanently off (manual mode)

# Ordered list shown to the user; "auto" is first so it is the default.
RELAY_STATES = [RELAY_STATE_AUTO, RELAY_STATE_ON, RELAY_STATE_OFF]

# ---------------------------------------------------------------------------
# Unit translation: CSV unit string → Home Assistant unit string
#
# The ProCon.IP CSV uses short ASCII unit strings.  Home Assistant expects
# specific Unicode strings (e.g. "°C" not "C") and has named constants for
# the most common ones.  The mapping here converts CSV units to HA-compatible
# strings so sensor entities report the correct units to the frontend and to
# the statistics database.
#
# Keys that are absent from this dict (unknown units from future firmware)
# fall back to the raw CSV string in sensor.py so no information is lost.
# ---------------------------------------------------------------------------
UNIT_MAP: dict[str, str | None] = {
    "C":   "°C",   # temperature  → UnitOfTemperature.CELSIUS
    "Bar": "bar",  # pressure     → UnitOfPressure.BAR
    "mV":  "mV",   # millivolts   → UnitOfElectricPotential.MILLIVOLT
    "pH":  "pH",   # pH value     – no HA named constant; use plain string
    "%":   "%",    # percentage   → PERCENTAGE constant
    "ml":  "mL",   # volume       – millilitres (HA uses capital L)
    "l/h": "L/h",  # flow rate    – no HA named constant; use plain string
    "h":   "h",    # hours        – internal processing timer
    "--":  None,   # dimensionless (relays, digital I/O) – no unit shown
    "":    None,   # blank unit column – treated the same as "--"
}

# ---------------------------------------------------------------------------
# Suggested display precision per HA unit string.
#
# ``SensorEntity.suggested_display_precision`` controls how many decimal
# places the HA frontend rounds the value to for display.  The actual stored
# value is always the full-precision float; this only affects rendering.
# ---------------------------------------------------------------------------
UNIT_PRECISION: dict[str, int] = {
    "°C":  1,  # e.g. 22.5 °C  – one decimal is sufficient for pool temps
    "bar": 3,  # e.g. 1.034 bar – boiler / filter pressure needs 3 decimals
    "mV":  0,  # e.g. 650 mV   – Redox readings are always whole millivolts
    "pH":  2,  # e.g. 7.35     – two decimals match typical pH meter displays
    "%":   0,  # e.g. 99 %     – canister fill level shown as integer percent
    "mL":  0,  # e.g. 0 mL     – consumption counter is a whole-number total
    "L/h": 0,  # e.g. 150 L/h  – flow sensor output is a whole number
    "h":   0,  # e.g. 2333 h   – internal timer is always a whole number
}
