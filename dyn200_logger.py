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
            torque_nm  REAL,
            speed_rpm  REAL,
            power_w    REAL
        )
    """)
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
        self.n_ok = 0
        self.n_err = 0
        # Ring buffers shared with the plot (last ~plot_window seconds)
        maxlen = max(100, int(args.plot_window / args.interval) + 10)
        self.buf_t      = collections.deque(maxlen=maxlen)
        self.buf_torque = collections.deque(maxlen=maxlen)
        self.buf_speed  = collections.deque(maxlen=maxlen)
        self.buf_power  = collections.deque(maxlen=maxlen)
        self.lock = threading.Lock()

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
                csv_writer.writerow(["ts_utc", "torque_nm", "speed_rpm",
                                     "power_w"])

        last_commit = time.monotonic()
        t_start = time.monotonic()

        while not self.stop_event.is_set():
            loop_start = time.monotonic()
            try:
                if self.tare_request.is_set():
                    self.tare_request.clear()
                    self.sensor.tare()
                    print("\nTared: current load is the new zero point.")
                torque, speed, power = self.sensor.read()
                ts = datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds")

                con.execute(
                    "INSERT INTO samples "
                    "(ts_utc, t_mono, torque_nm, speed_rpm, power_w) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts, loop_start, torque, speed, power))
                if csv_writer:
                    row = [f"{torque:.4f}", f"{speed:.1f}", f"{power:.1f}"]
                    if args.csv_excel:
                        row = [v.replace(".", ",") for v in row]
                    csv_writer.writerow([ts] + row)

                with self.lock:
                    self.buf_t.append(loop_start - t_start)
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
                last_commit = time.monotonic()

            remaining = self.args.interval - (time.monotonic() - loop_start)
            if remaining > 0:
                # Wait on the event so Ctrl+C / window close reacts fast
                self.stop_event.wait(remaining)

        con.commit()
        con.close()
        if csv_file:
            csv_file.close()


# ---------------------------------------------------------------------------
# Live plot
# ---------------------------------------------------------------------------
def run_plot(logger):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, figsize=(9, 8))
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

    def update(_frame):
        with logger.lock:
            t  = list(logger.buf_t)
            tq = list(logger.buf_torque)
            sp = list(logger.buf_speed)
            pw = list(logger.buf_power)
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
        # The serial port belongs to the logger thread, so only raise a
        # flag here; the logger tares between two reads.
        if event.key in ("t", "T"):
            logger.tare_request.set()

    fig.canvas.mpl_connect("key_press_event", on_key)
    fig.text(0.995, 0.005, "T = tare (set new zero)", ha="right",
             va="bottom", fontsize=8, alpha=0.6)

    ani = FuncAnimation(fig, update, interval=200, cache_frame_data=False)
    plt.tight_layout()
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
