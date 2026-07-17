# CLAUDE.md

Guidance for Claude Code (and future sessions) working in this repo.

## What this is

A single-file Python data logger for a **DYN-200 dynamic torque sensor**
(Shanghai QIYI). It reads torque, speed, and power over RS485 using
**Modbus RTU** through a USB-to-RS485 adapter (e.g. Waveshare USB TO RS485),
stores each sample in an **SQLite** database, and optionally appends to CSV
and/or shows a **live scrolling matplotlib plot** while logging.

The whole program is [dyn200_logger.py](dyn200_logger.py) (~317 lines).
User-facing docs are in [README.md](README.md); dependencies in
[requirements.txt](requirements.txt) (minimalmodbus, pyserial, matplotlib).

## How to run and test

**There is a `--demo` mode that fakes the sensor, so the full software stack
runs with no hardware attached. Always verify changes this way.**

```bash
python dyn200_logger.py --demo --plot     # full stack, fake data, live plot
```

Caveat for automated / non-interactive checks: `--plot` opens a **blocking**
matplotlib window that only closes on user action. For a quick programmatic
sanity check, run **without** `--plot` and stop it after a moment:

```bash
python dyn200_logger.py --demo            # prints a live status line; Ctrl+C to stop
```

On stop it prints a summary (`N samples logged, M comms errors`) and leaves a
`dyn200_data.sqlite` file. Real-hardware form:

```bash
python dyn200_logger.py --port COM5 --plot          # Windows
python dyn200_logger.py --port /dev/ttyUSB0 --plot  # Linux
```

## Protocol / hardware facts (durable domain knowledge)

Registers use **function code 03H**; each value is a **32-bit** quantity
spanning **2 registers** (read via `minimalmodbus.read_long`):

| Address  | Value  | Notes |
|----------|--------|-------|
| `0x0000` | Torque | 32-bit **signed**, scaled by `10^-decimals` → N·m |
| `0x0002` | Speed  | 32-bit unsigned, **0.1 RPM units** → raw/10 = RPM. The manual says "RPM" but is wrong — verified against the OLED 2026-07-17 |
| `0x0004` | Power  | 32-bit signed, raw × 10 → watts ("Power/10W") |

- **Tare / zero:** coil `0x0000` with **function code 05H** (`COIL_TARE`).
  Exposed via `--tare`, which zeroes the sensor before logging starts.
- **Serial defaults:** 38400 baud, **8 data bits, no parity, 2 stop bits (8N2)**,
  slave address 1. **Auto-baud:** if the config read gets no answer,
  `RealSensor` tries all four supported rates (38400/19200/14400/9600) and
  keeps the one that responds — the user's actual sensor is set to **19200**.
- The sensor must have **parameter 09 = 1** (Modbus RTU mode) or it won't talk.
- The torque scale (**parameter 03**, decimal-point setting) is **read from
  the sensor at startup** (register `0x0008`), along with filter, direction,
  and factor (`0x0006`, `0x0012`, `0x001A`) — printed as a config banner.
  `--decimals` overrides it; if the config read fails, fallback is 2.
- **Tare mid-run:** pressing **T** in the plot window sets
  `logger.tare_request` (a `threading.Event`); the logger thread performs the
  tare between reads. Never call the sensor from the plot thread directly.

## Code map

- `RealSensor` / `DemoSensor` — swappable sensor backends sharing the same
  `read()` → `(torque_nm, speed_rpm, power_w)` and `tare()` interface.
  `--demo` selects `DemoSensor` (sine waves + noise); otherwise `RealSensor`.
- `make_instrument()` — minimalmodbus/pyserial setup (baud, 8N2, timeout).
  `import minimalmodbus`/`serial` are done inside the function so `--demo`
  works even if those packages aren't installed.
- `open_db()` — creates the `samples` table
  (`ts_utc, t_mono, torque_nm, speed_rpm, power_w`) and an index; returns a
  connection.
- `Logger.run()` — the acquisition loop: read → insert (+ optional CSV) →
  update shared ring buffers → sleep the remainder of `--interval`.
- `run_plot()` — matplotlib `FuncAnimation` live plot (torque + speed).
- `main()` — argument parsing and wiring.

**Important design points to preserve when editing the loop or plot:**

- **Threading:** with `--plot`, `Logger.run()` runs in a **daemon thread** and
  the plot runs on the main thread. `Logger.run()` **opens its own SQLite
  connection inside the thread** — sqlite connections must not cross threads.
  Don't move `open_db()` out of `run()`.
- **Shared state:** the plot reads `buf_t` / `buf_torque` / `buf_speed`
  (bounded `deque` ring buffers) under `self.lock`. Keep that lock around any
  shared-buffer access.
- **Responsiveness:** the loop waits on `stop_event` (not `time.sleep`) so
  Ctrl+C and window-close react quickly. DB commits and CSV flushes are
  **batched ~once per second**, not per sample.
- Comms errors are caught per-iteration, counted in `n_err`, and retried after
  a short pause — one bad read never kills the run.

## Conventions & working notes

- Keep it a **single, dependency-light file**; favor **readable over clever**.
- **Windows-first** environment (PowerShell). Serial ports look like `COMx`.
- The maintainer is a **novice developer** — when making changes, explain what
  you're doing and why, flag common pitfalls, and keep the code approachable.
- Recurring real-world pitfalls (also in the README troubleshooting section):
  - Timeout / CRC errors → most often the **A/B wires are swapped**; also check
    baud, stop bits, and parameter 09 = 1.
  - Torque off by 10× / 100× → `--decimals` doesn't match sensor parameter 03.
  - USB adapter carries data only; the sensor needs its **own 24 V supply**.
