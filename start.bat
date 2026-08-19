@echo off
setlocal EnableExtensions
title Naturalizer
cd /d "%~dp0"

echo.
echo  ============================================
echo    Naturalizer  -  AI text humanizer
echo  ============================================
echo.

REM ---------- 1. Find a working Python ---------------------------------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if defined PY (
    %PY% --version >nul 2>nul || set "PY="
)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] Python was not found or is not usable.
    echo  Install Python 3.9 or newer from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

REM ---------- 2. Pick a port (8000, or the next free one) ----------------
if not defined PORT set "PORT=8000"
:pickport
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul
if errorlevel 1 goto portfree
set /a PORT+=1
if %PORT% gtr 8999 (
    echo  [ERROR] No free port between 8000 and 8999.
    pause
    exit /b 1
)
goto pickport
:portfree

REM ---------- 3. Start the server and open the browser -------------------
echo  Starting Naturalizer on http://127.0.0.1:%PORT% ...
echo  Keep this window open. Press Ctrl+C to stop the server.
echo.
start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%PORT%/ & exit"
%PY% server.py

echo.
echo  Server stopped.
pause
