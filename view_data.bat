@echo off
rem Opens a saved DYN-200 run as a diagram - no Excel needed.
rem Lists the CSV files sitting next to this script, newest first, and
rem plots the one you pick. The plot window has zoom, pan and a save
rem button in its toolbar. Close it to return here.

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

"%EXE%" --view --csv-dir "%HERE%"
pause
