@echo off
setlocal EnableExtensions
title AI Text Humanizer
cd /d "%~dp0"

echo.
echo  =====================================================
echo    AI Text Humanizer  ^|  Powered by Naturalizer
echo  =====================================================
echo.

REM ---------- 1. Find a working Python ----------------------------------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if defined PY (
    %PY% --version >nul 2>nul || set "PY="
)
if not defined PY (
    echo  [ERROR] Python was not found or is not usable.
    echo  Install Python 3.9 or newer from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

echo  [OK] Found Python:
%PY% --version
echo.

REM ---------- 2. Install the package so naturalizer is importable ----------
%PY% -c "import naturalizer" >nul 2>nul
if errorlevel 1 (
    echo  [INFO] Installing naturalizer package...
    %PY% -m pip install -e . --quiet 2>nul
    if errorlevel 1 (
        echo  [WARN] pip install failed, falling back to PYTHONPATH
        set "PYTHONPATH=%~dp0;%PYTHONPATH%"
    )
)

REM ---------- 3. Kill any existing server on port 8000 -----------------
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo  [INFO] Killing existing process on port 8000 (PID %%a)...
    taskkill /F /PID %%a >nul 2>nul
)

REM ---------- 4. Pick a free port (8000 first, then 8001..8099) ---------
set "PORT=8000"
:pickport
netstat -ano 2>nul | findstr /r /c:":%PORT% .*LISTENING" >nul 2>nul
if not errorlevel 1 (
    set /a PORT+=1
    if %PORT% gtr 8099 (
        echo  [ERROR] No free port found between 8000 and 8099.
        pause
        exit /b 1
    )
    goto pickport
)

REM ---------- 5. Open browser after a short delay -----------------------
echo  [INFO] Starting server on http://127.0.0.1:%PORT% ...
echo  [INFO] Your browser will open automatically in 2 seconds.
echo.
echo  -------------------------------------------------------
echo   Press Ctrl+C in this window to stop the server.
echo  -------------------------------------------------------
echo.

start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%PORT%/ & exit"

REM ---------- 6. Launch server ------------------------------------------
set PORT=%PORT%
%PY% server.py

echo.
echo  [INFO] Server stopped.
pause
