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

Don't know the baud rate? You don't need to: if the sensor doesn't
answer at the expected rate (default 38400), the logger automatically
tries the other rates the DYN-200 supports and tells you which one
worked.

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

Every sample is stored with **two** time columns:

| Column | Meaning |
|---|---|
| `ts_utc` | Absolute UTC timestamp — says *when* the sample was taken |
| `t_s` | **Seconds since that run started** (0.000, 0.200, 0.400, …) — the ready-made x axis for charts |

Use `t_s` whenever you want a diagram with time on the horizontal axis.
In Excel, the CSV columns are `ts_utc | t_s | torque_nm | speed_rpm |
power_w`, so selecting columns **B through E** and inserting a scatter
chart gives you `t_s` on the x axis and the three measurements as
series — no formula needed.

The SQLite file loads straight into pandas:

```python
import sqlite3, pandas as pd
df = pd.read_sql("SELECT * FROM samples", sqlite3.connect("dyn200_data.sqlite"),
                 parse_dates=["ts_utc"])
df.plot(x="t_s", y="torque_nm")     # seconds on the x axis
```

Note that a database accumulates *many* runs, and `t_s` restarts from
zero on each one — so filter to a single run (by `ts_utc` range) before
plotting against `t_s`. Each CSV holds exactly one run, so it needs no
filtering.

## Troubleshooting

- **Timeout / CRC errors:** swap A and B wires (most common cause), check
  baud rate and stop bits, confirm sensor parameter 09 = 1 (Modbus mode).
- **Torque values off by 10x/100x:** shouldn't happen anymore — the
  decimal setting is read from the sensor at startup. If it does (e.g.
  the config read failed), pass `--decimals` to match the sensor's
  parameter 03 (shown on the OLED).
- **Can't find the port (Windows):** Device Manager → Ports (COM & LPT),
  unplug/replug the adapter to see which COM number appears.
