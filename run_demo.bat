@echo off
rem Starts the DYN-200 logger in DEMO mode (fake data, no hardware needed)
rem with the live plot, so you can try the buttons and see how the CSV
rem files look before taking a real measurement.
rem
rem In the plot window:
rem   Start / Stop  save one CSV per measurement, into this folder
rem   View          opens the measurement you just saved as a diagram
rem   Tare          sets the current load as the new zero
rem
rem Demo output stays SEPARATE from real measurements: files are named
rem dyn200_DEMO_*.csv and the database is dyn200_DEMO.sqlite, so fake
rem data can never land in the real dyn200_data.sqlite.

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

"%EXE%" --demo --plot --db "%HERE%\dyn200_DEMO.sqlite" --csv-dir "%HERE%" --csv-prefix dyn200_DEMO --csv-excel
pause
