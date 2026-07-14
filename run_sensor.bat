@echo off
rem Starts the DYN-200 logger on the REAL sensor with the live plot.
rem It first lists the serial ports found on this PC so you can pick the
rem USB-RS485 adapter. Close the plot window to stop logging.

if exist "%~dp0dyn200_logger.exe" (
    set "EXE=%~dp0dyn200_logger.exe"
) else if exist "%~dp0dist\dyn200_logger.exe" (
    set "EXE=%~dp0dist\dyn200_logger.exe"
) else (
    echo Could not find dyn200_logger.exe next to this script or in dist\.
    echo Build it first ^(see BUILDING.md^) or copy the exe into this folder.
    pause
    exit /b 1
)

rem Timestamp for the output files, e.g. 2026-07-14_10-30-00 (PowerShell
rem is used because %date%/%time% formats vary with Windows language).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"

rem Each run gets its own CSV (opens directly in Excel) next to this
rem script. The SQLite database keeps everything as well.
"%EXE%" --plot --csv "%~dp0dyn200_run_%TS%.csv" --csv-excel
pause
