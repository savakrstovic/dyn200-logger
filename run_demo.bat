@echo off
rem Starts the DYN-200 logger in DEMO mode (fake data, no hardware needed)
rem with the live plot. Close the plot window to stop logging.

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

"%EXE%" --demo --plot
pause
