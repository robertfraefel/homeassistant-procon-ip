# ProCon.IP Pool Controller – Home Assistant Integration

A custom Home Assistant integration for the [ProCon.IP](https://github.com/ylabonte/procon-ip) network-attached pool management unit.

---

## Features

| Platform | Description |
|---|---|
| **Sensor** | Temperature, pH, Redox, pressure, flow rate, canister fill levels, chemical consumption |
| **Select** | Per-relay mode control: **Auto / On / Off** |
| **Binary Sensor** | Digital inputs (buttons, pool-cover status, …) |

All entity names and units are read directly from the device's `GetState.csv` so they automatically match your local configuration (including German labels).

---

## Requirements

- Home Assistant **2023.1** or later
- ProCon.IP pool controller reachable on the local network
- The controller's HTTP interface enabled (default: port 80)

---

## Installation

### Via HACS (recommended)

1. Open HACS → **Integrations** → ⋮ menu → **Custom repositories**
2. Add `https://github.com/robertfraefel/homeassistant-procon-ip` as type **Integration**
3. Search for *ProCon.IP* and install it
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/procon_ip/` folder into your Home Assistant config directory:
   ```
   <config>/custom_components/procon_ip/
   ```
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **ProCon.IP Pool Controller**
3. Fill in the form:

| Field | Default | Description |
|---|---|---|
| Host / IP address | `192.168.3.17` | IP or hostname of the ProCon.IP unit |
| Port | `80` | HTTP port |
| Username | `admin` | Basic-auth username (leave empty to disable auth) |
| Password | `admin` | Basic-auth password |
| Update interval | `30` | Polling interval in seconds |

---

## Entities

### Sensors (examples based on default ProCon.IP layout)

| Name | Unit | HA Device Class |
|---|---|---|
| Pool | °C | Temperature |
| Absorber / Rücklauf / Aussen | °C | Temperature |
| CPU Temp | °C | Temperature |
| Kesseldruck | bar | Pressure |
| Redox | mV | Voltage |
| pH | pH | – |
| Durchfluss | L/h | – |
| Cl Rest / pH- Rest / pH+ Rest | % | – |
| Cl consumption / pH- consumption / pH+ consumption | mL | – |

Columns labelled `n.a.` on the device are automatically skipped.

### Select (relay control)

One **Select** entity per active relay.
Available options:

| Option | Behaviour |
|---|---|
| `auto` | Hand control back to the ProCon.IP schedule / dosage logic |
| `on` | Force relay **on** (manual mode) |
| `off` | Force relay **off** (manual mode) |

> **Tip:** Use `auto` to let the ProCon.IP manage filter pumps on its own timer.

### Binary Sensors

Digital inputs with no numeric unit (e.g. buttons, pool-cover position switch) appear as binary sensors.

---

## How it works

The integration polls `/GetState.csv` at the configured interval.
Each CSV row contains:

```
Row 0  SYSINFO   – firmware version & device ID
Row 1  Names     – label for each column
Row 2  Units     – unit string for each column
Row 3  Offsets   – calibration offset
Row 4  Factors   – scale factor
Row 5  Raws      – raw integer values
```

**Displayed value = offset + factor × raw**

Relay control is done via a `POST` to `/usrcfg.cgi` with an `ENA` parameter encoding the on/manual bit patterns for all relays simultaneously (same protocol used by the official ProCon.IP web interface and the [procon-ip](https://github.com/ylabonte/procon-ip) TypeScript library).

---

## Dashboard

A ready-made Lovelace dashboard is included in [`dashboards/pool_dashboard.yaml`](dashboards/pool_dashboard.yaml).
It provides three views out of the box:

| View | Contents |
|---|---|
| **Overview** | Live gauges for temperature, pH and Redox; relay controls; canister fill levels; digital inputs |
| **History** | 48 h temperature trends, 7-day pH / Redox graphs, 30-day canister level history |
| **Controls** | Full relay control panel for all internal (N1–N8) and external (E1–E8) relays |

### Import steps

**Option A – Paste into a new YAML dashboard**

1. Go to **Settings → Dashboards → Add dashboard**
2. Choose *YAML dashboard*, give it the title **Pool**
3. Paste the contents of `pool_dashboard.yaml` into the raw-config editor

**Option B – Reference as a file dashboard**

1. Copy `dashboards/pool_dashboard.yaml` to your HA config directory
2. Add to `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    pool-dashboard:
      mode: yaml
      filename: dashboards/pool_dashboard.yaml
      title: Pool
      icon: mdi:pool
```

3. Restart Home Assistant

### Adapting entity IDs

Entity IDs are derived from the device name and the CSV column labels on your ProCon.IP.
With the default device name **"ProCon.IP Pool Controller"** the slug is
`procon_ip_pool_controller`, giving entity IDs like:

```
sensor.procon_ip_pool_controller_pool
sensor.procon_ip_pool_controller_ph
select.procon_ip_pool_controller_filterpumpe_n1
```

If you renamed your device or your CSV labels differ, do a global find-and-replace of
`procon_ip_pool_controller` with your actual device slug in `pool_dashboard.yaml`.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "Failed to connect" | Check IP, port, and that the device is reachable (try opening `http://<ip>/GetState.csv` in a browser) |
| "Invalid authentication" | Verify username/password; try leaving both empty if your device has no auth |
| Entities show `unavailable` | Check HA logs; the device may have stopped responding |
| Relay control has no effect | Ensure firmware ≥ 1.7.0; the `/usrcfg.cgi` endpoint was stabilised in that release |

---

## License

MIT
