#!/usr/bin/env python3
"""
DYN-200 Dynamic Torque Sensor data logger with live plotting
============================================================

Reads torque / speed / power from a DYN-200 sensor over RS485
(Modbus RTU) using a USB-RS485 adapter (e.g. Waveshare USB TO RS485),
stores samples in an SQLite database (+ optional CSV), and can show a
live scrolling plot while logging.

Register map (from the DYN-200 manual, function code 03H):
    0x0000  Torque  (32-bit signed, scaled by the "decimal" setting, N·m)
    0x0002  Speed   (32-bit, 0.1 RPM units -> raw / 10 = RPM; the manual
                     says "RPM" but the OLED shows 10x less than the raw
                     register value, verified by hand 2026-07-17)
    0x0004  Power   (32-bit signed, raw = watts; the manual's "Power/10W"
                     is wrong, verified against the OLED 2026-07-23)

Sensor serial defaults: 38400 baud, 8 data bits, no parity, 2 stop bits,
slave address 1.

Dependencies:
    pip install minimalmodbus pyserial matplotlib

Usage examples:
    python dyn200_logger.py --demo --plot            # no hardware needed!
    python dyn200_logger.py --port COM5 --plot       # Windows
    python dyn200_logger.py --port /dev/ttyUSB0 --plot --csv run1.csv
"""

import argparse
import collections
import csv
import math
import os
import random
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Register addresses (per DYN-200 manual)
# ---------------------------------------------------------------------------
REG_TORQUE = 0x0000   # 2 registers, signed
REG_SPEED  = 0x0002   # 2 registers, unsigned, 0.1 RPM units (not RPM!)
REG_POWER  = 0x0004   # 2 registers, signed; raw = watts (NOT "Power/10W")
COIL_TARE  = 0x0000   # function 05H: build new zero (tare)

# Configuration registers (also read with function code 03H)
REG_FILTER    = 0x0006   # digital filter level, 1-100
REG_DECIMALS  = 0x0008   # decimal-point setting, 0-4 (sensor parameter 03)
REG_DIRECTION = 0x0012   # torque direction: 0 = default, 1 = opposite
REG_FACTOR    = 0x001A   # calibration factor


# ---------------------------------------------------------------------------
# Sensor access
# ---------------------------------------------------------------------------
def make_instrument(port, baud, slave, stopbits, timeout=0.3):
    import minimalmodbus
    import serial
    inst = minimalmodbus.Instrument(port, slave, mode=minimalmodbus.MODE_RTU)
    inst.serial.baudrate = baud
    inst.serial.bytesize = 8
    inst.serial.parity = serial.PARITY_NONE
    inst.serial.stopbits = stopbits
    inst.serial.timeout = timeout
    inst.clear_buffers_before_each_transaction = True
    return inst


def pick_port():
    """List the serial ports found on this PC and let the user pick one.
    Used when --port wasn't given (and we're not in --demo mode)."""
    from serial.tools import list_ports

    while True:
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        if ports:
            break
        print("No serial ports found. Is the USB-RS485 adapter plugged in?")
        try:
            input("Plug it in, then press Enter to scan again "
                  "(Ctrl+C quits): ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nNo serial port selected.")

    print("Serial ports found:")
    for i, p in enumerate(ports, start=1):
        print(f"  {i}. {p.device}  ({p.description})")

    while True:
        try:
            answer = input(f"Select port [1-{len(ports)}, "
                           f"Enter = 1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nNo serial port selected.")
        if answer == "":
            choice = 1
        else:
            try:
                choice = int(answer)
            except ValueError:
                # Also accept typing the name itself, e.g. "COM11"
                matches = [p for p in ports
                           if p.device.lower() == answer.lower()]
                if matches:
                    return matches[0].device
                print("Please enter a number from the list.")
                continue
        if 1 <= choice <= len(ports):
            return ports[choice - 1].device
        print("Please enter a number from the list.")


class RealSensor:
    # The four baud rates the DYN-200 supports (its parameter 08)
    BAUD_RATES = [38400, 19200, 14400, 9600]

    def __init__(self, args):
        self.inst = make_instrument(args.port, args.baud, args.slave,
                                    args.stopbits)
        cfg = self._read_config()
        if cfg is None:
            # No answer - maybe the sensor is set to a different baud
            # rate. Try the others (a wrong rate fails in ~0.3 s).
            for baud in self.BAUD_RATES:
                if baud == args.baud:
                    continue
                self.inst.serial.baudrate = baud
                cfg = self._read_config()
                if cfg is not None:
                    print(f"No answer at {args.baud} baud - sensor found "
                          f"at {baud} baud (its parameter 08).")
                    break
            else:
                self.inst.serial.baudrate = args.baud

        decimals = args.decimals   # None unless --decimals was given
        if cfg is None:
            if decimals is None:
                decimals = 2
            print(f"Could not read sensor config at any baud rate; "
                  f"assuming decimals={decimals}.\n"
                  f"  If logging also fails, check wiring and sensor "
                  f"settings.")
        else:
            print(f"Sensor config: decimals={cfg['decimals']}, "
                  f"filter={cfg['filter']}, "
                  f"direction={'opposite' if cfg['direction'] else 'default'}, "
                  f"factor={cfg['factor']}")
            if decimals is None:
                decimals = cfg["decimals"]
            elif decimals != cfg["decimals"]:
                print(f"Note: --decimals {decimals} overrides the sensor's "
                      f"own setting of {cfg['decimals']}.")
        self.torque_scale = 10 ** (-decimals)

    def _read_config(self):
        """Read the config registers; return a dict, or None if the sensor
        doesn't answer (or answers nonsense)."""
        try:
            cfg = {
                "decimals":  self.inst.read_long(REG_DECIMALS, functioncode=3),
                "filter":    self.inst.read_long(REG_FILTER, functioncode=3),
                "direction": self.inst.read_long(REG_DIRECTION, functioncode=3),
                "factor":    self.inst.read_long(REG_FACTOR, functioncode=3),
            }
        except Exception:
            return None
        if not 0 <= cfg["decimals"] <= 4:
            return None   # got bytes, but not believable ones
        return cfg

    def read(self):
        raw_torque = self.inst.read_long(REG_TORQUE, functioncode=3, signed=True)
        raw_speed  = self.inst.read_long(REG_SPEED,  functioncode=3, signed=False)
        raw_power  = self.inst.read_long(REG_POWER,  functioncode=3, signed=True)
        # Speed AND power both use scalings the manual gets wrong; both
        # verified against the OLED display and a physics check (the
        # mechanical power |torque| * omega must match the power reading):
        #   speed: register is in 0.1 RPM units  -> raw / 10 = RPM
        #   power: register is already in watts   -> raw as-is. The manual
        #          labels it "Power/10W"; multiplying by 10 read 10x high.
        return (raw_torque * self.torque_scale,   # N·m
                raw_speed / 10.0,                 # RPM
                float(raw_power))                 # W

    def tare(self):
        self.inst.write_bit(COIL_TARE, 1, functioncode=5)


class DemoSensor:
    """Generates plausible fake data so you can test without hardware."""
    def __init__(self, _args):
        self.t0 = time.monotonic()
        self.torque_offset = 0.0

    def _raw_torque(self):
        t = time.monotonic() - self.t0
        return 12 + 4 * math.sin(t / 3)

    def read(self):
        t = time.monotonic() - self.t0
        torque = (self._raw_torque() - self.torque_offset
                  + random.gauss(0, 0.15))
        speed  = 1450 + 60 * math.sin(t / 7) + random.gauss(0, 5)
        power  = torque * speed * 2 * math.pi / 60  # P = T * omega
        return torque, speed, power

    def tare(self):
        # Mimic the real sensor: whatever is measured now becomes zero.
        self.torque_offset = self._raw_torque()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def open_db(path):
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc     TEXT    NOT NULL,
            t_mono     REAL    NOT NULL,
            t_s        REAL,
            torque_nm  REAL,
            speed_rpm  REAL,
            power_w    REAL
        )
    """)
    # Databases written before t_s existed only have the old columns, and
    # SQLite has no "ADD COLUMN IF NOT EXISTS" - so look before leaping.
    # Rows logged back then keep t_s = NULL; new rows get the real value.
    columns = [row[1] for row in con.execute("PRAGMA table_info(samples)")]
    if "t_s" not in columns:
        con.execute("ALTER TABLE samples ADD COLUMN t_s REAL")
    con.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts_utc)")
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Acquisition loop (runs in a background thread when plotting)
# ---------------------------------------------------------------------------
class Logger:
    def __init__(self, sensor, args):
        self.sensor = sensor
        self.args = args
        self.stop_event = threading.Event()
        self.tare_request = threading.Event()   # set by the plot's T key
        # Recording control. The plot's Start/Stop buttons only raise these
        # flags; the logger thread does the actual file work between two
        # reads, so the CSV is only ever touched by one thread.
        self.start_record_request = threading.Event()
        self.stop_record_request = threading.Event()
        # Segment file state - owned by the logger thread alone
        self._seg_file = None
        self._seg_writer = None
        self._seg_path = None
        self._seg_t0 = None
        # Shared with the plot; read and written under self.lock
        self.recording = False    # is a measurement being written right now?
        self.seg_rows = 0         # samples in the measurement being recorded
        self.n_saved = 0          # measurements finished this session
        self.last_saved = None    # path of the most recently finished one
        self.n_ok = 0
        self.n_err = 0
        # Ring buffers shared with the plot (last ~plot_window seconds)
        maxlen = max(100, int(args.plot_window / args.interval) + 10)
        self.buf_t      = collections.deque(maxlen=maxlen)
        self.buf_torque = collections.deque(maxlen=maxlen)
        self.buf_speed  = collections.deque(maxlen=maxlen)
        self.buf_power  = collections.deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def _segment_start(self):
        """Begin a new measurement CSV (the plot's Start button).

        Each measurement gets its own timestamped file, exactly like
        closing and reopening the window used to - except the window
        stays open and the database keeps recording throughout."""
        if self._seg_file is not None:
            return                       # already recording; ignore
        os.makedirs(self.args.csv_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.args.csv_dir,
                            f"{self.args.csv_prefix}_{stamp}.csv")
        self._seg_file = open(path, "w", newline="")
        self._seg_writer = csv.writer(
            self._seg_file, delimiter=";" if self.args.csv_excel else ",")
        self._seg_writer.writerow(["ts_utc", "t_s", "torque_nm",
                                   "speed_rpm", "power_w"])
        self._seg_path = path
        self._seg_t0 = None              # set from the first sample below
        with self.lock:
            self.recording = True
            self.seg_rows = 0
        print(f"\nRecording -> {os.path.basename(path)}")

    def _segment_stop(self):
        """Finish the current measurement CSV (the plot's Stop button).

        Also called when the run ends, so closing the window while still
        recording saves the file instead of losing it."""
        if self._seg_file is None:
            return
        rows, path = self.seg_rows, self._seg_path
        self._seg_file.close()
        self._seg_file = self._seg_writer = self._seg_path = None
        self._seg_t0 = None
        with self.lock:
            self.recording = False
            self.n_saved += 1
            self.last_saved = path
        print(f"\nSaved {os.path.basename(path)}  ({rows} samples)")

    def run(self):
        """Poll the sensor until stop_event is set. Owns its own DB handle
        (sqlite connections must stay on one thread)."""
        args = self.args
        con = open_db(args.db)

        csv_file = csv_writer = None
        if args.csv:
            try:
                open(args.csv, "r").close()
                new_file = False
            except FileNotFoundError:
                new_file = True
            csv_file = open(args.csv, "a", newline="")
            csv_writer = csv.writer(
                csv_file, delimiter=";" if args.csv_excel else ",")
            if new_file:
                csv_writer.writerow(["ts_utc", "t_s", "torque_nm",
                                     "speed_rpm", "power_w"])

        last_commit = time.monotonic()
        t_start = time.monotonic()

        while not self.stop_event.is_set():
            loop_start = time.monotonic()
            try:
                if self.tare_request.is_set():
                    self.tare_request.clear()
                    self.sensor.tare()
                    print("\nTared: current load is the new zero point.")
                if self.start_record_request.is_set():
                    self.start_record_request.clear()
                    self._segment_start()
                if self.stop_record_request.is_set():
                    self.stop_record_request.clear()
                    self._segment_stop()
                torque, speed, power = self.sensor.read()
                ts = datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds")

                # Seconds since this run started: the ready-made x axis
                # for Excel and pandas. Same clock the live plot uses, so
                # the two always agree.
                t_rel = loop_start - t_start

                con.execute(
                    "INSERT INTO samples "
                    "(ts_utc, t_mono, t_s, torque_nm, speed_rpm, power_w) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, loop_start, t_rel, torque, speed, power))
                if csv_writer:
                    row = [f"{t_rel:.3f}", f"{torque:.4f}", f"{speed:.1f}",
                           f"{power:.1f}"]
                    if args.csv_excel:
                        row = [v.replace(".", ",") for v in row]
                    csv_writer.writerow([ts] + row)

                if self._seg_writer is not None:
                    # A measurement file's clock starts at its own Start
                    # press, so every saved run begins at t_s = 0.
                    if self._seg_t0 is None:
                        self._seg_t0 = loop_start
                    seg_row = [f"{loop_start - self._seg_t0:.3f}",
                               f"{torque:.4f}", f"{speed:.1f}",
                               f"{power:.1f}"]
                    if args.csv_excel:
                        seg_row = [v.replace(".", ",") for v in seg_row]
                    self._seg_writer.writerow([ts] + seg_row)
                    with self.lock:
                        self.seg_rows += 1

                with self.lock:
                    self.buf_t.append(t_rel)
                    self.buf_torque.append(torque)
                    self.buf_speed.append(speed)
                    self.buf_power.append(power)

                self.n_ok += 1
                if not args.quiet and not args.plot:
                    sys.stdout.write(
                        f"\r{ts}  torque {torque:9.3f} N·m   "
                        f"speed {speed:8.1f} RPM   power {power:9.1f} W   "
                        f"(ok {self.n_ok} / err {self.n_err})   ")
                    sys.stdout.flush()

            except Exception as e:
                self.n_err += 1
                if not args.quiet:
                    sys.stdout.write(f"\rComms error ({e}); retrying...     ")
                    sys.stdout.flush()
                time.sleep(0.5)

            if time.monotonic() - last_commit > 1.0:
                con.commit()
                if csv_file:
                    csv_file.flush()
                if self._seg_file:
                    self._seg_file.flush()
                last_commit = time.monotonic()

            remaining = self.args.interval - (time.monotonic() - loop_start)
            if remaining > 0:
                # Wait on the event so Ctrl+C / window close reacts fast
                self.stop_event.wait(remaining)

        con.commit()
        con.close()
        if csv_file:
            csv_file.close()
        # Stopping mid-measurement saves it rather than losing it
        self._segment_stop()


# ---------------------------------------------------------------------------
# Reading saved runs back (the diagram viewer)
# ---------------------------------------------------------------------------
def read_run_csv(path):
    """Read a CSV written by this logger back into plain Python lists.

    Copes with both dialects - plain (comma columns, dot decimals) and the
    --csv-excel form (semicolon columns, comma decimals) - and with older
    four-column files from before the t_s column existed; for those the
    time axis is rebuilt from the timestamps."""
    with open(path, "r", newline="") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(f"{path} has no data rows")

    header = lines[0]
    # The Excel dialect separates columns with ';' and writes decimals with
    # ',', so whichever appears more often in the header is the separator.
    delim = ";" if header.count(";") > header.count(",") else ","
    decimal_comma = delim == ";"
    names = [c.strip() for c in header.split(delim)]
    idx = {name: i for i, name in enumerate(names)}
    # Drop any short final line (a run cut off mid-write still opens fine)
    rows = [r for r in (ln.split(delim) for ln in lines[1:])
            if len(r) == len(names)]
    if not rows:
        raise ValueError(f"{path} has no complete data rows")

    def number(text):
        return float(text.replace(",", ".") if decimal_comma else text)

    def column(name):
        i = idx[name]
        return [number(r[i]) for r in rows]

    data = {"torque": column("torque_nm"),
            "speed":  column("speed_rpm"),
            "power":  column("power_w")}
    if "t_s" in idx:
        data["t"] = column("t_s")
    else:
        # Pre-v0.3.0 file: derive elapsed seconds from the UTC timestamps
        stamps = [datetime.fromisoformat(r[idx["ts_utc"]]) for r in rows]
        data["t"] = [(s - stamps[0]).total_seconds() for s in stamps]
    return data


def plot_run(path, blocking=True):
    """Open one saved CSV as a three-panel diagram.

    The matplotlib toolbar gives zoom, pan and save-as-PNG for free.
    blocking=False is used by the live window's View button, which must
    not freeze the logger behind it."""
    import matplotlib.pyplot as plt

    data = read_run_csv(path)
    t, tq, sp, pw = data["t"], data["torque"], data["speed"], data["power"]
    mean = lambda xs: sum(xs) / len(xs)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, figsize=(9, 8))
    fig.canvas.manager.set_window_title(f"DYN-200 - {os.path.basename(path)}")
    ax1.plot(t, tq, lw=1.0)
    ax2.plot(t, sp, lw=1.0, color="tab:orange")
    ax3.plot(t, pw, lw=1.0, color="tab:green")
    ax1.set_ylabel("Torque [N\u00b7m]")
    ax2.set_ylabel("Speed [RPM]")
    ax3.set_ylabel("Power [W]")
    ax3.set_xlabel("Time [s]")
    for ax in (ax1, ax2, ax3):
        ax.grid(True, alpha=0.3)
    ax1.set_title(
        f"{os.path.basename(path)}   -   {t[-1]:.1f} s, {len(t)} samples\n"
        f"torque mean {mean(tq):.3f} / max {max(tq):.3f} N\u00b7m     "
        f"speed mean {mean(sp):.0f} RPM     power mean {mean(pw):.0f} W",
        fontsize=10)
    fig.tight_layout()
    if blocking:
        plt.show()      # blocks until the window is closed
    else:
        fig.show()      # returns at once, so live logging carries on
    return fig


def pick_csv(directory="."):
    """List the CSV files in a folder, newest first, and let the user pick
    one. Used by --view when no file name was given."""
    import glob

    files = sorted(glob.glob(os.path.join(directory, "*.csv")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        sys.exit(f"No .csv files found in {os.path.abspath(directory)}.")

    print(f"Saved runs in {os.path.abspath(directory)} (newest first):")
    for i, f in enumerate(files, start=1):
        when = datetime.fromtimestamp(
            os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        print(f"  {i:2}. {os.path.basename(f):<44} {when}  "
              f"{os.path.getsize(f) / 1024:7.1f} kB")

    while True:
        try:
            answer = input(f"Select run [1-{len(files)}, Enter = 1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nNothing selected.")
        if answer == "":
            return files[0]
        try:
            choice = int(answer)
        except ValueError:
            # Also accept typing the file name itself
            matches = [f for f in files
                       if os.path.basename(f).lower() == answer.lower()]
            if matches:
                return matches[0]
            print("Please enter a number from the list.")
            continue
        if 1 <= choice <= len(files):
            return files[choice - 1]
        print("Please enter a number from the list.")


# ---------------------------------------------------------------------------
# Live plot
# ---------------------------------------------------------------------------
def run_plot(logger):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.widgets import Button

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, figsize=(9, 8.6))
    fig.canvas.manager.set_window_title("DYN-200 live data")

    (line_torque,) = ax1.plot([], [], lw=1.2)
    (line_speed,)  = ax2.plot([], [], lw=1.2, color="tab:orange")
    (line_power,)  = ax3.plot([], [], lw=1.2, color="tab:green")

    ax1.set_ylabel("Torque [N·m]")
    ax2.set_ylabel("Speed [RPM]")
    ax3.set_ylabel("Power [W]")
    ax3.set_xlabel("Time [s]")
    ax1.grid(True, alpha=0.3)
    ax2.grid(True, alpha=0.3)
    ax3.grid(True, alpha=0.3)
    title = ax1.set_title("waiting for data...")
    status = fig.text(0.985, 0.062, "", ha="right", va="center", fontsize=9)

    def update(_frame):
        with logger.lock:
            t  = list(logger.buf_t)
            tq = list(logger.buf_torque)
            sp = list(logger.buf_speed)
            pw = list(logger.buf_power)
            recording = logger.recording
            seg_rows  = logger.seg_rows
            n_saved   = logger.n_saved
        if recording:
            status.set_text(f"\u25cf RECORDING - {seg_rows} samples")
            status.set_color("tab:red")
        else:
            status.set_text(f"idle - {n_saved} measurement(s) saved")
            status.set_color("0.35")
        if not t:
            return line_torque, line_speed, line_power
        line_torque.set_data(t, tq)
        line_speed.set_data(t, sp)
        line_power.set_data(t, pw)
        ax1.set_xlim(max(0, t[-1] - logger.args.plot_window),
                     max(t[-1], logger.args.plot_window))
        ax1.relim(); ax1.autoscale_view(scalex=False)
        ax2.relim(); ax2.autoscale_view(scalex=False)
        ax3.relim(); ax3.autoscale_view(scalex=False)
        title.set_text(
            f"torque {tq[-1]:.3f} N·m    speed {sp[-1]:.0f} RPM    "
            f"power {pw[-1]:.0f} W    "
            f"(ok {logger.n_ok} / err {logger.n_err})")
        return line_torque, line_speed, line_power

    def on_key(event):
        # The serial port and the CSV belong to the logger thread, so only
        # raise a flag here; the logger acts on it between two reads.
        if event.key in ("t", "T"):
            logger.tare_request.set()
        elif event.key in ("r", "R"):
            with logger.lock:
                recording = logger.recording
            if recording:
                logger.stop_record_request.set()
            else:
                logger.start_record_request.set()

    fig.canvas.mpl_connect("key_press_event", on_key)

    # Lay the plots out first, then reserve a strip at the bottom for the
    # buttons - tight_layout would otherwise try to arrange them too.
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.165)

    def add_button(left, label, color, hover):
        ax = fig.add_axes([left, 0.035, 0.135, 0.055])
        return Button(ax, label, color=color, hovercolor=hover)

    btn_start = add_button(0.035, "\u25cf Start", "#cfe9cf", "#a5d8a5")
    btn_stop  = add_button(0.180, "\u25a0 Stop",  "#f2cccc", "#e5a5a5")
    btn_view  = add_button(0.325, "View",       "#ccd8ef", "#a5badd")
    btn_tare  = add_button(0.470, "Tare",       "#e3e3e3", "#c6c6c6")

    def on_start(_evt):
        logger.start_record_request.set()

    def on_stop(_evt):
        logger.stop_record_request.set()

    def on_tare(_evt):
        logger.tare_request.set()

    def on_view(_evt):
        # Button callbacks run on the GUI thread, so opening another
        # window here is safe; the logger thread is untouched.
        with logger.lock:
            path = logger.last_saved
        if path is None:
            print("\nNothing saved yet - press Start, then Stop, then View.")
            return
        try:
            plot_run(path, blocking=False)
        except Exception as e:
            print(f"\nCould not open {path}: {e}")

    btn_start.on_clicked(on_start)
    btn_stop.on_clicked(on_stop)
    btn_view.on_clicked(on_view)
    btn_tare.on_clicked(on_tare)
    # matplotlib drops buttons that nothing refers to, so keep a reference
    fig._dyn200_buttons = (btn_start, btn_stop, btn_view, btn_tare)

    fig.text(0.015, 0.005, "R = start/stop recording    T = tare",
             ha="left", va="bottom", fontsize=8, alpha=0.6)

    ani = FuncAnimation(fig, update, interval=200, cache_frame_data=False)
    plt.show()   # blocks until the window is closed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="DYN-200 torque sensor logger")
    ap.add_argument("--port",
                    help="Serial port, e.g. COM5 or /dev/ttyUSB0. If omitted "
                         "(and not --demo), lists detected ports to pick from")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--stopbits", type=int, default=2, choices=[1, 2])
    ap.add_argument("--decimals", type=int, default=None,
                    help="Override the sensor's decimal-point setting "
                         "(parameter 03). Normally not needed: it is read "
                         "from the sensor at startup (2 if that fails)")
    ap.add_argument("--interval", type=float, default=0.2,
                    help="Polling interval in seconds (default 0.2 = 5 Hz)")
    ap.add_argument("--db", default="dyn200_data.sqlite")
    ap.add_argument("--csv", default=None,
                    help="Optional CSV file to also append samples to")
    ap.add_argument("--csv-dir", default=".",
                    help="Folder that the Start/Stop buttons write their "
                         "measurement CSVs into (default: current folder)")
    ap.add_argument("--csv-prefix", default="dyn200_run",
                    help="Name stem for the Start/Stop measurement files "
                         "(default dyn200_run -> dyn200_run_<timestamp>.csv)")
    ap.add_argument("--view", nargs="?", const="", default=None,
                    metavar="FILE",
                    help="Don't log: open a saved CSV as a diagram. With no "
                         "file name, lists the CSVs in --csv-dir to pick from")
    ap.add_argument("--csv-excel", action="store_true",
                    help="Write the CSV for Excel on European-locale "
                         "Windows: semicolons between columns, decimal "
                         "commas (12,34 instead of 12.34)")
    ap.add_argument("--tare", action="store_true",
                    help="Zero the sensor before logging starts")
    ap.add_argument("--plot", action="store_true",
                    help="Show a live scrolling plot while logging")
    ap.add_argument("--plot-window", type=float, default=30.0,
                    help="Seconds of history shown in the plot (default 30)")
    ap.add_argument("--demo", action="store_true",
                    help="Generate fake data (no hardware needed)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.view is not None:
        # Viewer mode: no sensor, no database - just look at a saved run.
        plot_run(args.view or pick_csv(args.csv_dir))
        return

    if not args.demo and not args.port:
        # No port given: show what's connected and ask, instead of erroring.
        args.port = pick_port()

    sensor = DemoSensor(args) if args.demo else RealSensor(args)

    if args.tare:
        print("Taring sensor (setting new zero point)...")
        sensor.tare()
        time.sleep(0.5)

    logger = Logger(sensor, args)
    # Report the baud the connection actually uses (auto-baud may have
    # picked a different one than requested)
    src = ("DEMO data" if args.demo
           else f"{args.port} @ {sensor.inst.serial.baudrate} baud")
    print(f"Logging from {src} -> {args.db}"
          + (f" and {args.csv}" if args.csv else ""))

    if args.plot:
        thread = threading.Thread(target=logger.run, daemon=True)
        thread.start()
        print("Close the plot window to stop logging.")
        print("Start/Stop save one CSV per measurement; View opens the "
              "last one.")
        try:
            run_plot(logger)
        finally:
            logger.stop_event.set()
            thread.join(timeout=3)
    else:
        print("Press Ctrl+C to stop.\n")
        try:
            logger.run()
        except KeyboardInterrupt:
            logger.stop_event.set()

    print(f"\nStopped. {logger.n_ok} samples logged, "
          f"{logger.n_err} comms errors.")


if __name__ == "__main__":
    main()
