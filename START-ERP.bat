@echo off
REM ===================================================================
REM  Apparel ERP - double-click this file to start.
REM  Closes nothing, installs nothing, needs no internet.
REM ===================================================================
setlocal
cd /d "%~dp0"
title Apparel ERP

REM Find a usable Python. The py launcher ships with the python.org
REM installer and is the most reliable; fall back to python/python3.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python  >nul 2>&1 && set "PY=python"  )
if not defined PY ( where python3 >nul 2>&1 && set "PY=python3" )

if not defined PY (
  echo.
  echo   Python is not installed on this computer.
  echo.
  echo   1. Go to  https://www.python.org/downloads/
  echo   2. Download Python for Windows and run the installer
  echo   3. IMPORTANT: tick "Add Python to PATH" on the first screen
  echo   4. Then double-click this file again
  echo.
  pause
  exit /b 1
)

REM Refuse early and clearly on an ancient Python rather than dying
REM halfway through with a confusing syntax error.
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Your Python is too old. This needs Python 3.10 or newer.
  %PY% --version
  echo   Install the latest from https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting the ERP. Your browser will open in a moment.
echo   Leave this window open while you use it.
echo.

%PY% demo\erp_server.py --open

echo.
echo   The ERP has stopped.
pause
