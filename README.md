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
`dyn200_logger.exe` together with the three launcher scripts to the
target PC and double-click:

- **`run_demo.bat`** — fake data + live plot, no hardware needed. Good
  for trying the buttons and seeing what a CSV looks like.
- **`run_sensor.bat`** — real sensor + live plot. It first lists the
  serial ports found on the PC so you can pick the USB-RS485 adapter.
- **`view_data.bat`** — opens a saved run as a diagram. No logging, no
  sensor, no Excel needed.

Close the plot window to stop logging; the data lands in
`dyn200_data.sqlite` next to the scripts.

## Recording measurements

The live plot window has four buttons along the bottom:

| Button | What it does |
|---|---|
| **● Start** | Begins a measurement. A new `dyn200_run_<timestamp>.csv` is opened and every sample from now on goes into it. |
| **■ Stop** | Ends the measurement and closes that CSV. Press Start again for the next one. |
| **View** | Opens the measurement you just saved as a diagram, in its own window. Logging carries on behind it. |
| **Tare** | Sets the current load as the new zero point. |

The status on the right shows `● RECORDING - n samples` while a
measurement is running, and how many you have saved when it is not.
Keyboard shortcuts: **R** starts/stops recording, **T** tares.

This replaces the old "close the window to get your CSV" workflow — the
window now stays open for a whole session and you save each measurement
as its own file, with its own time axis starting at zero.

Two things worth knowing:

- **The database records everything, continuously**, whether or not you
  are recording a measurement. The buttons only control the CSV files,
  so forgetting to press Start never loses data from the archive.
- **Closing the window mid-measurement saves it** rather than throwing
  it away.

## Viewing a saved run as a diagram

You do not need Excel to look at a measurement. Double-click
**`view_data.bat`** (or run `dyn200_logger.py --view`): it lists the CSV
files in the folder, newest first, and plots the one you pick as the
same three panels — torque, speed and power against time.

```bash
python dyn200_logger.py --view                    # pick from a list
python dyn200_logger.py --view dyn200_run_...csv  # open one directly
```

The window's toolbar gives you zoom, pan, and save-as-PNG. The title
line summarises the run: duration, sample count, mean and maximum
torque, mean speed and mean power.

Older four-column CSVs from before the `t_s` column existed open fine
too — the time axis is rebuilt from their timestamps.

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
| `--csv run1.csv` | Also append *every* sample to one CSV, continuously (independent of the Start/Stop buttons) |
| `--csv-excel` | CSV dialect for European-locale Excel (semicolons, decimal commas) |
| `--csv-dir data\` | Folder the Start/Stop buttons write measurements into (default: current folder) |
| `--csv-prefix test` | Name stem for those files (default `dyn200_run`) |
| `--view [FILE]` | Don't log — open a saved CSV as a diagram. No file name means pick from a list |
| `--tare` | Zero the sensor before logging (same as long-press K3) |
| `--decimals 2` | Override the sensor's decimal-point setting (normally read automatically at startup) |
| `--plot-window 60` | Seconds of history shown in the live plot |
| `--db mydata.sqlite` | Database file name |

Close the plot window (or press Ctrl+C when not plotting) to stop.

While the plot is open, press **T** (or the Tare button) to tare — the
current load becomes the new zero point, without restarting the logger.
On connect, the logger also prints the sensor's configuration (decimals,
filter, direction, factor) so every run records how the sensor was set
up.

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
