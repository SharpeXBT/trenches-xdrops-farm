@echo off
title TRENCHES XDROPS FARM - installation
echo.
echo  Checking Python...
python --version >nul 2>&1
if %errorlevel%==0 (
    echo  Python is already installed:
    python --version
    goto ready
)
echo  Python not found - installing via winget (Windows package manager)...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
if %errorlevel%==0 (
    echo.
    echo  Python installed. CLOSE this window, then run INSTALL_WINDOWS.bat
    echo  once more to verify (a fresh window is needed to see Python).
    pause
    exit /b
)
echo.
echo  Automatic install failed. Install Python manually:
echo    1. Open  https://www.python.org/downloads/
echo    2. Download, run the installer, TICK "Add python.exe to PATH"
echo    3. Run this file again.
start https://www.python.org/downloads/
pause
exit /b

:ready
echo.
echo  Everything is ready. Next steps:
echo    1. Open bot.py (in this folder) with Notepad, scroll to the bottom,
echo       fill in API_KEY / API_SECRET / API_PASSPHRASE.
echo    2. Double-click START.bat (this folder)
echo.
pause
