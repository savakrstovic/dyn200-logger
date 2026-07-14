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

"%EXE%" --plot
pause
