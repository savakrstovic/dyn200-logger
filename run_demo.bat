@echo off
rem Starts the DYN-200 logger in DEMO mode (fake data, no hardware needed)
rem with the live plot. Close the plot window to stop logging.
rem
rem Demo output is kept SEPARATE from real measurements: fake samples go
rem to dyn200_DEMO.sqlite and a dyn200_DEMO_*.csv, never to the real
rem dyn200_data.sqlite / dyn200_run_*.csv that run_sensor.bat writes.

rem ==================================================================
rem  Write a CSV file for each demo run?   yes = write it, no = don't.
rem  (Change only this one word, then save the file.)
set "MAKE_CSV=yes"
rem ==================================================================

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

rem Build the CSV part of the command line only if MAKE_CSV says yes.
rem When it says no, CSV_ARGS stays empty and the logger writes just the
rem database - exactly like this script did before.
set "CSV_ARGS="
if /i "%MAKE_CSV%"=="yes" set "CSV_ARGS=--csv "%~dp0dyn200_DEMO_%TS%.csv" --csv-excel"

"%EXE%" --demo --plot --db "%~dp0dyn200_DEMO.sqlite" %CSV_ARGS%

echo.
if /i "%MAKE_CSV%"=="yes" echo Demo data written to dyn200_DEMO_%TS%.csv
if /i not "%MAKE_CSV%"=="yes" echo No CSV written - MAKE_CSV is set to %MAKE_CSV%. Data is in dyn200_DEMO.sqlite
pause
