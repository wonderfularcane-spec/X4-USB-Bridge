#!/usr/bin/env python3
"""Build a portable two-executable Windows beta release.

This script only writes inside this repository; it never opens a COM port.
Prerequisite: Windows, Python 3.11+, pip and Internet access to PyPI.
"""
import os
import struct
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0-beta.2"
VENV = ROOT / ".build-venv"
BUILD_PYTHON = VENV / "Scripts" / "python.exe"
DIST = ROOT / "dist"


def call(*argv):
    print("+", " ".join(map(str, argv)), flush=True)
    subprocess.run([str(arg) for arg in argv], cwd=ROOT, check=True)


def main():
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise SystemExit("A 64-bit Windows Python interpreter is required.")
    if not BUILD_PYTHON.is_file():
        print("Creating isolated Python environment ...", flush=True)
        venv.create(VENV, with_pip=True)
    call(BUILD_PYTHON, "-m", "pip", "install", "--disable-pip-version-check",
         "--requirement", ROOT / "build-requirements.txt")

    for exe_name, entrypoint, mode in (
        ("X4_USB_Bridge_Worker", "x4_usb_bridge.py", "--console"),
        ("X4_USB_Bridge", "x4_usb_bridge_gui.py", "--windowed"),
    ):
        call(BUILD_PYTHON, "-m", "PyInstaller", "--noconfirm",
             "--onefile", mode, "--name", exe_name, entrypoint)

    worker = DIST / "X4_USB_Bridge_Worker.exe"
    gui = DIST / "X4_USB_Bridge.exe"
    call(worker, "--help")
    call(gui, "--self-test")

    destination = DIST / ("X4-USB-Bridge-v" + VERSION + "-windows-x64.zip")
    quickstart = (
        "X4 USB Bridge v" + VERSION + " - Windows portable preview\r\n"
        "\r\n"
        "IMPORTANT: experimental, with no real USB unplug/replug validation yet.\r\n"
        "1. Ensure the printer is idle and cool. Close PuTTY/HW VSP3.\r\n"
        "2. Stop any existing v0.1 bridge before starting this new version.\r\n"
        "3. Keep both .exe files together in one folder.\r\n"
        "4. Open X4_USB_Bridge.exe, choose CH340 COM port and click Start.\r\n"
        "5. OrcaSlicer URL: http://127.0.0.1:8181/\r\n"
        "6. Avoid real printing until this beta passes link testing.\r\n"
        "\r\n"
        "The printer firmware is not modified. This runs a temporary\r\n"
        "Python relay in the existing Linux serial console.\r\n"
        "See README.md for detailed usage and safety information.\r\n"
    )

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(gui, gui.name)
        archive.write(worker, worker.name)
        archive.write(ROOT / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")
        archive.write(ROOT / "CHANGELOG.md", "CHANGELOG.md")
        archive.writestr("START_HERE.txt", quickstart.encode("utf-8"))

    print("Release ready:", destination, flush=True)
    print("Size:", destination.stat().st_size, "bytes", flush=True)


if __name__ == "__main__":
    main()
