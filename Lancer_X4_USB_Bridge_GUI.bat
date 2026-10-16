@echo off
setlocal
cd /d "%~dp0"
py -c "import tkinter, serial" >nul 2>nul
if errorlevel 1 (
    echo Python 3 and pyserial are required.
    echo Run: py -m pip install -r requirements.txt
    pause
    exit /b 1
)
start "" pyw "%~dp0x4_usb_bridge_gui.py"
