# Code walkthrough: dyn200_logger.py

A guided tour of the logger's source, top to bottom, explaining what each
part does and *why* it's written that way. Read it side-by-side with
[dyn200_logger.py](dyn200_logger.py).

## The big picture

The program is a loop with three optional attachments:

```
                 ┌─────────────┐
  DYN-200 ──────►│  Sensor     │      read() every --interval seconds
  (or fake data) │  object     │
                 └──────┬──────┘
                        ▼
                 ┌─────────────┐      one row per sample
                 │ Logger.run()│─────► SQLite (always)
                 │  the loop   │─────► CSV    (--csv)
                 └──────┬──────┘
                        ▼ ring buffers (last ~30 s)
                 ┌─────────────┐
                 │  run_plot() │      redraws 5×/second (--plot)
                 └─────────────┘
```

Five building blocks, in file order: register constants → sensor classes →
database setup → the `Logger` loop → the plot → `main()` wiring it together.

---

## 1. Imports and constants (top of file)

Everything imported at the top is from Python's **standard library** —
`argparse` (command-line flags), `collections` (ring buffers), `csv`,
`math`/`random` (fake data), `sqlite3`, `threading`, `time`, `datetime`.

The three *third-party* packages are deliberately **not** imported here:

- `minimalmodbus` and `serial` are imported inside `make_instrument()`
- `matplotlib` is imported inside `run_plot()`

This is a "lazy import" pattern: the import only happens if that feature is
actually used. Consequence: you can run `--demo` on a machine that doesn't
even have the Modbus libraries installed.

The constants mirror the DYN-200 manual's register map:

```python
REG_TORQUE = 0x0000   # 2 registers, signed
REG_SPEED  = 0x0002   # 2 registers, unsigned
REG_POWER  = 0x0004   # 2 registers, signed ("Power/10W")
COIL_TARE  = 0x0000   # function 05H: build new zero (tare)
```

Each value is 32 bits, but Modbus registers are 16 bits — so each reading
spans **two consecutive registers**. That's why the addresses step by 2 and
why the code later uses `read_long` (which reads a register *pair*) rather
than plain `read_register`.

Note that `COIL_TARE` is also address `0x0000` but does **not** clash with
`REG_TORQUE`: Modbus coils and holding registers are separate address
spaces, selected by the function code (05H vs 03H).

## 2. `make_instrument()` — opening the serial port

Creates a `minimalmodbus.Instrument`: a Python object representing "slave
device number N on this serial port". The lines after it set the serial
parameters to the DYN-200's defaults — 38400 baud, 8 data bits, no parity,
2 stop bits ("8N2").

Two settings deserve explanation:

- `timeout = 0.3` — if the sensor doesn't answer within 0.3 s, the read
  raises an exception rather than hanging forever. Serial code without a
  timeout freezes the whole program the moment a wire comes loose.
- `clear_buffers_before_each_transaction = True` — discards any stale
  bytes sitting in the OS serial buffer before each request. Without it, a
  garbled earlier reply can poison the *next* read (classic source of
  "CRC check failed" errors that mysteriously fix themselves).

## 3. `RealSensor` — talking to the hardware

The constructor opens the port and precomputes the torque scale factor:

```python
self.torque_scale = 10 ** (-args.decimals)
```

The sensor transmits torque as an integer; where the decimal point goes is
a *sensor-side display setting* (parameter 03). With `--decimals 2`, a raw
value of `1234` means `12.34` N·m. If readings are ever off by exactly
10× or 100×, this mismatch is why.

`read()` performs three Modbus transactions:

```python
raw_torque = self.inst.read_long(REG_TORQUE, functioncode=3, signed=True)
```

- `read_long` = read two consecutive 16-bit registers, combine into one
  32-bit value (the "two registers per value" fact from section 1).
- `signed=True` for torque and power because both can be negative
  (direction of twist; power flowing "backwards"). Speed is unsigned.
- Scaling: torque × 10^-decimals → N·m; power × 10 → watts (the manual
  says the register holds "power / 10 W"); speed is RPM as-is.

`tare()` writes bit 1 to coil 0 with function code 05H — the electronic
equivalent of long-pressing the K3 button on the sensor: "current load is
the new zero".

## 4. `DemoSensor` — the fake twin

Same two methods as `RealSensor` — `read()` and `tare()` — which is the
whole point. The rest of the program takes *either* object and never knows
which one it got. Python calls this **duck typing**: no interface
declaration needed, matching method names suffice. This is what makes
`--demo` a genuine full-stack test: only the bottom-most layer is swapped.

The fake data is built to look alive rather than random:

```python
torque = 12 + 4 * math.sin(t / 3) + random.gauss(0, 0.15)
speed  = 1450 + 60 * math.sin(t / 7) + random.gauss(0, 5)
power  = torque * speed * 2 * math.pi / 60   # P = T·ω
```

Slow sine waves (periods of ~19 s and ~44 s) plus Gaussian noise mimic a
motor under varying load. Power is derived from the real physics formula
— P [W] = torque [N·m] × angular velocity [rad/s] — so the three plotted
curves stay physically consistent with each other.

`time.monotonic()` is used instead of `time.time()`: it's a clock that
only ever moves forward, immune to NTP adjustments and DST changes. The
whole file uses it for *durations* and `datetime.now(timezone.utc)` for
*timestamps* — a distinction worth copying in your own code.

## 5. `open_db()` — the database

Creates (if needed) one table:

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-numbered row id |
| `ts_utc` | TEXT | ISO-8601 UTC wall-clock time, ms precision |
| `t_mono` | REAL | Monotonic seconds — for computing exact intervals |
| `torque_nm`, `speed_rpm`, `power_w` | REAL | The readings, in real units |

`CREATE TABLE IF NOT EXISTS` makes the function *idempotent* — safe to call
against an existing database; it just opens it. The index on `ts_utc`
makes time-range queries ("give me 14:00–14:05") fast even after millions
of rows.

## 6. `Logger` — the heart of the program

### `__init__`: shared state

```python
maxlen = max(100, int(args.plot_window / args.interval) + 10)
self.buf_t      = collections.deque(maxlen=maxlen)
self.buf_torque = collections.deque(maxlen=maxlen)
self.buf_speed  = collections.deque(maxlen=maxlen)
self.buf_power  = collections.deque(maxlen=maxlen)
```

A `deque` with `maxlen` is a **ring buffer**: appending to a full one
silently drops the oldest entry. Sized to hold exactly one plot-window's
worth of samples (plus slack), these hold the *recent* data for the plot —
memory usage stays constant no matter how long you log. The full history
goes to SQLite; the deques are just the plot's window into the last ~30 s.

Two threading primitives are created here:

- `stop_event` — a `threading.Event`, i.e. a thread-safe boolean flag.
  Anyone can call `.set()` to politely ask the loop to finish.
- `lock` — guards the deques. The logger thread appends while the plot
  thread reads; the lock ensures neither sees the buffers mid-update.

### `run()`: the acquisition loop

**It opens its own database connection.** SQLite connections must be used
only on the thread that created them. Since `run()` may execute on a
background thread (plot mode), it creates the connection *inside* itself
rather than receiving one. Don't refactor this away.

**CSV setup** checks whether the file already exists — the header row is
written only for a brand-new file, so re-running with the same `--csv`
appends data without a duplicate header in the middle.

**The loop body**, one iteration per sample:

1. `loop_start = time.monotonic()` — timestamp the iteration start.
2. `sensor.read()` — get the three values (Modbus or fake).
3. `INSERT` into SQLite, using `?` placeholders (the safe, canonical way
   to pass values into SQL).
4. Optionally write a CSV row.
5. Append to the ring buffers **under the lock**.
6. Print the one-line live status (`\r` returns the cursor to the start
   of the line, so it updates in place instead of scrolling).

**Error handling:** the whole body sits in `try/except`. A failed read —
loose wire, EMI glitch, sensor power blip — increments `n_err`, prints a
note, sleeps 0.5 s, and *continues*. One bad sample never kills a long
logging run. The ok/err counters give an honest health report at the end.

**Batched commits:**

```python
if time.monotonic() - last_commit > 1.0:
    con.commit()
```

Each SQLite `commit()` forces a disk sync — doing that 5× per second
hammers the disk for no benefit. Instead, inserts accumulate and are
committed once per second. Worst case after a power cut: the last second
of data is lost. The CSV file is flushed on the same rhythm.

**Pacing:**

```python
remaining = self.args.interval - (time.monotonic() - loop_start)
if remaining > 0:
    self.stop_event.wait(remaining)
```

Sleep only for whatever is *left* of the interval after the work is done,
so the sample rate stays close to the target regardless of how long the
Modbus read took. And it "sleeps" by waiting **on the stop event** rather
than `time.sleep()` — if someone calls `stop_event.set()`, the wait aborts
immediately instead of finishing the nap. That's why Ctrl+C and closing
the plot window feel instant.

After the loop: final commit, close everything. No data is left dangling.

## 7. `run_plot()` — the live plot

Runs on the **main thread** (a matplotlib requirement) while `Logger.run()`
works in the background. Three stacked charts sharing an x-axis: torque,
speed, power.

The interesting part is `update()`, called every 200 ms by
`FuncAnimation`:

```python
with logger.lock:
    t  = list(logger.buf_t)
    tq = list(logger.buf_torque)
    sp = list(logger.buf_speed)
    pw = list(logger.buf_power)
```

It **copies** the buffers while holding the lock, then does all the slow
drawing *after* releasing it. The lock is held for microseconds, so the
logger thread is never blocked waiting for a redraw. All four copies
happen under one lock acquisition so the lists are guaranteed equal
length — copying them separately could catch the logger mid-append and
crash matplotlib with mismatched x/y sizes.

The rest is presentation: `set_xlim` slides the window so the newest data
hugs the right edge; `relim()`/`autoscale_view(scalex=False)` re-fits the
y-axes only; the title text doubles as a live numeric readout.

`plt.show()` **blocks** until the window closes — that's the mechanism by
which "close the window" ends the program (see `main()` below).

## 8. `main()` — wiring it together

In order:

1. **Argparse** defines every flag with a default matching the sensor's
   factory settings, so the minimal real-hardware invocation is just
   `--port COM11`.
2. **Validation:** `--port` is required *unless* `--demo` — enforced with
   `ap.error(...)`, which prints usage and exits.
3. **Sensor selection** — the one line that decides real vs fake:
   ```python
   sensor = DemoSensor(args) if args.demo else RealSensor(args)
   ```
4. **Optional tare**, with a 0.5 s pause to let the sensor settle.
5. **Two run modes:**
   - **With `--plot`:** `logger.run()` goes into a background thread
     (`daemon=True` = "don't let this thread keep the process alive"),
     and the plot takes the main thread. When the window closes,
     `plt.show()` returns, the `finally:` block sets `stop_event`, and
     `thread.join(timeout=3)` waits for the logger to finish its final
     commit. This is a textbook clean-shutdown pattern.
   - **Without `--plot`:** `logger.run()` runs directly on the main
     thread; Ctrl+C raises `KeyboardInterrupt`, which is caught and turned
     into a `stop_event.set()`.
6. **Exit summary** — samples logged and comms errors, the run's health
   at a glance.

## Design patterns worth stealing

- **Duck-typed backends** (`RealSensor`/`DemoSensor`) — swap hardware for
  a simulator by matching method names; everything above stays untouched.
- **Lazy imports** — optional features don't punish users who skip them.
- **Ring buffers for UI, database for history** — constant memory, full
  record.
- **Copy-under-lock, work-outside-lock** — hold locks briefly.
- **Event-based sleeping** — responsive shutdown for free.
- **Batched disk syncs** — throughput without hammering the disk.
- **Monotonic clock for durations, UTC for timestamps** — never confuse
  the two jobs of "time".
