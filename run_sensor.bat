@echo off
rem Starts the DYN-200 logger on the REAL sensor with the live plot.
rem It first lists the serial ports found on this PC so you can pick the
rem USB-RS485 adapter.
rem
rem In the plot window:
rem   Start / Stop  save one CSV per measurement, into this folder
rem   View          opens the measurement you just saved as a diagram
rem   Tare          sets the current load as the new zero
rem
rem The SQLite database records everything continuously either way, so
rem nothing is lost if you forget to press Start. Close the window to
rem stop logging (a measurement still running is saved, not discarded).

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

rem This script's own folder. %~dp0 ends with a backslash, which would
rem escape the closing quote when passed as "%~dp0", so trim it off.
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

"%EXE%" --plot --csv-dir "%HERE%" --csv-excel
pause
