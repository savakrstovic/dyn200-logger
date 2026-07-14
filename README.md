# DYN-200 Torque Sensor Logger

Reads torque, speed, and power from a **DYN-200 dynamic torque sensor**
over RS485 (Modbus RTU) using a USB-RS485 adapter (e.g. Waveshare USB TO
RS485). Samples are stored in an SQLite database (optionally CSV too) and
can be viewed on a live scrolling plot while logging.

## Hardware setup

| Sensor wire | Connect to |
|---|---|
| Red | +24 V DC power supply |
| Black | Power supply GND |
| Yellow (RS485 A) | Adapter terminal **A** |
| Blue (RS485 B) | Adapter terminal **B** |

The USB adapter carries data only — the sensor needs its own 24 V supply.

Sensor communication defaults: **38400 baud, 8 data bits, no parity,
2 stop bits, slave address 1**. Parameter 09 on the sensor must be set to
`1` (Modbus RTU mode).

## Quick start (no Python needed)

If you have the standalone build (see [BUILDING.md](BUILDING.md)), copy
`dyn200_logger.exe` together with the two launcher scripts to the target
PC and double-click:

- **`run_demo.bat`** — fake data + live plot, no hardware needed.
- **`run_sensor.bat`** — real sensor + live plot. It first lists the
  serial ports found on the PC so you can pick the USB-RS485 adapter.

Close the plot window to stop logging; the data lands in
`dyn200_data.sqlite` next to the scripts.

## Install

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

## Usage

Test everything without hardware (fake data):

```bash
python dyn200_logger.py --demo --plot
```

Log from the real sensor with a live plot:

```bash
python dyn200_logger.py --port COM5 --plot        # Windows
python dyn200_logger.py --port /dev/ttyUSB0 --plot  # Linux
```

Don't know the port? Leave out `--port` — the logger lists the serial
ports it finds (with descriptions) and asks you to pick one.

Useful options:

| Flag | Meaning |
|---|---|
| `--interval 0.1` | Sample every 0.1 s (10 Hz). Default 0.2 s |
| `--csv run1.csv` | Also append samples to a CSV file |
| `--csv-excel` | CSV dialect for European-locale Excel (semicolons, decimal commas) |
| `--tare` | Zero the sensor before logging (same as long-press K3) |
| `--decimals 2` | Override the sensor's decimal-point setting (normally read automatically at startup) |
| `--plot-window 60` | Seconds of history shown in the live plot |
| `--db mydata.sqlite` | Database file name |

Close the plot window (or press Ctrl+C when not plotting) to stop.

While the plot is open, press **T** to tare — the current load becomes
the new zero point, without restarting the logger. On connect, the
logger also prints the sensor's configuration (decimals, filter,
direction, factor) so every run records how the sensor was set up.

## Analyzing logged data

The SQLite file loads straight into pandas:

```python
import sqlite3, pandas as pd
df = pd.read_sql("SELECT * FROM samples", sqlite3.connect("dyn200_data.sqlite"),
                 parse_dates=["ts_utc"])
df.plot(x="ts_utc", y="torque_nm")
```

## Troubleshooting

- **Timeout / CRC errors:** swap A and B wires (most common cause), check
  baud rate and stop bits, confirm sensor parameter 09 = 1 (Modbus mode).
- **Torque values off by 10x/100x:** shouldn't happen anymore — the
  decimal setting is read from the sensor at startup. If it does (e.g.
  the config read failed), pass `--decimals` to match the sensor's
  parameter 03 (shown on the OLED).
- **Can't find the port (Windows):** Device Manager → Ports (COM & LPT),
  unplug/replug the adapter to see which COM number appears.
