@echo off
cd /d "%~dp0"
title Artillery X4 USB Bridge - local
py -c "import serial" >nul 2>&1
if errorlevel 1 (
  echo Python pyserial manque. Installer avec: py -m pip install pyserial
  pause
  exit /b 1
)
py -u "%~dp0x4_usb_bridge.py" --com COM3 --port 8181
echo.
echo Pont arrete.
pause
