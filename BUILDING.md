# Building dyn200_logger.exe — step-by-step

This documents exactly how the standalone Windows executable was produced
from `dyn200_logger.py`, what tools were used and why, and how to repeat
the build after code changes. Written 2026-07-13.

## What was used

| Tool / library | Version used | Role |
|---|---|---|
| Windows 11 Pro | 10.0.26200 | Build machine OS (the exe targets Windows) |
| Python | 3.13 | The language the logger is written in |
| pip | (bundled with Python) | Installs the packages below |
| minimalmodbus | 2.1.1 | Talks Modbus RTU to the DYN-200 over serial |
| pyserial | 3.5 | Serial-port access; minimalmodbus builds on it |
| matplotlib | ≥3.7 | The live scrolling plot GUI |
| PyInstaller | 6.21.0 | Bundles Python + all of the above into one .exe |

`sqlite3`, `csv`, `threading`, `argparse` etc. are part of Python's
standard library — nothing extra to install for those.

## Why PyInstaller?

The lab PC shouldn't need Python, pip, or any packages installed.
PyInstaller solves that: it analyzes the script's imports, collects the
Python interpreter plus every needed library into a bundle, and wraps it
in a single `.exe`. Copy that one file anywhere and it just runs.

Trade-offs accepted:
- **Size:** ~39 MB, mostly matplotlib. Fine for a USB stick.
- **Startup:** a `--onefile` exe unpacks itself to a temp folder on every
  launch, so the first few seconds are silent. Normal, not a hang.

## The steps, in order

### 1. Install the runtime dependencies

```
pip install -r requirements.txt
```

This installs minimalmodbus, pyserial, and matplotlib. Everything must be
importable *before* building — PyInstaller bundles what's installed in
your Python environment; it cannot bundle what isn't there.

### 2. Verify the script itself works

```
python dyn200_logger.py --demo --plot
```

`--demo` generates fake sensor data, so the entire stack (logging loop,
SQLite writes, live plot) is exercised with no hardware attached.
**Never build an exe from untested code** — a bundled bug is the same bug,
just harder to debug.

### 3. Install PyInstaller

```
pip install pyinstaller
```

### 4. Build

```
pyinstaller --onefile --name dyn200_logger dyn200_logger.py
```

What the flags mean:
- `--onefile` — produce a single self-extracting `.exe` instead of a
  folder full of DLLs. Simpler to carry around.
- `--name dyn200_logger` — the output filename.

No `--noconsole` flag: this is deliberately a **console app**. The status
line, error messages, and Ctrl+C handling all live in the console window.

The build takes about a minute and creates:

| Path | What it is | Keep? |
|---|---|---|
| `dist/dyn200_logger.exe` | **The product** — the only file you need | Yes |
| `build/` | PyInstaller's intermediate work files | No — safe to delete |
| `dyn200_logger.spec` | Auto-generated build recipe | No — the command above recreates it |

All three are listed in `.gitignore`, so none of this ends up in git
(a 39 MB binary does not belong in version control).

### 5. Test the exe — twice

```
dist\dyn200_logger.exe --demo            # core: logging loop + SQLite
dist\dyn200_logger.exe --demo --plot     # GUI: matplotlib bundled OK?
```

The second test matters most: matplotlib's GUI backend is the piece most
likely to break inside a bundled exe (typical symptom: an error about a
missing backend the moment the window should open). Both tests passed on
this build — the plot run logged 241 samples with 0 errors.

### 6. Deploy

Copy `dist\dyn200_logger.exe` **plus the two launcher scripts**
(`run_demo.bat`, `run_sensor.bat`, in the repo root) into one folder on
the target PC. Then just double-click:

- `run_demo.bat` — demo mode with live plot (no hardware).
- `run_sensor.bat` — real sensor with live plot. The logger lists the
  serial ports it finds and asks which one to use, so nobody has to know
  the COM number in advance.

You can also run the exe from a terminal (cmd or PowerShell) to pass
flags yourself:

```
dyn200_logger.exe --port COM11 --plot
```

If `--port` is omitted (and `--demo` isn't used), the port picker
appears. To find the COM number manually: Device Manager → Ports
(COM & LPT), or unplug/replug the RS485 adapter and watch which entry
appears.

## Rebuilding after code changes

Only steps 2, 4, and 5 are needed again:

```
python dyn200_logger.py --demo --plot                       # test source
pyinstaller --onefile --name dyn200_logger dyn200_logger.py # rebuild
dist\dyn200_logger.exe --demo --plot                        # test exe
```

If you add a new `import` of a third-party package, `pip install` it
first — see step 1's note.

## Troubleshooting the exe

- **Antivirus flags or deletes it.** PyInstaller `--onefile` exes are a
  known false-positive for strict AV, because self-extracting binaries
  resemble malware droppers. Fixes, in order: add an AV exclusion for the
  file; or rebuild without `--onefile` (a `--onedir` build makes a folder
  with the exe plus DLLs — less compact, but AV-friendlier).
- **"Failed to execute script" popup or instant exit.** Run it from a
  terminal instead of double-clicking — the real Python error will be
  printed there.
- **Takes ~5–10 s to start.** Inherent to `--onefile` (self-extraction);
  see trade-offs above.
- **Must the build PC match the lab PC?** Same OS family and bitness:
  build on 64-bit Windows for 64-bit Windows. An exe built here will not
  run on Linux/Mac.
