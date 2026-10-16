# X4 USB Bridge

**Control an Artillery Sidewinder X4 Plus from OrcaSlicer using its USB-C serial connection — without Wi-Fi, LAN access or printer firmware changes.**

> **Experimental v0.1 prototype:** verified on one printer and PC, not yet a fully supported release. Supervise printing and review the limitations before use.

## How it works

OrcaSlicer expects Moonraker's HTTP/WebSocket API. The CH340 USB-C port on the tested X4 Plus exposes a Linux serial **console**, not a network interface. Conventional virtual COM-port software cannot convert that console into an HTTP server.

~~~text
OrcaSlicer / Fluidd (Windows)
       | HTTP / WebSocket / G-code upload
       v
127.0.0.1:8181 -- X4 USB Bridge (Python)
       | serial protocol
       v
USB-C / CH340 / COM3 (1500000 baud)
       | existing Linux console
       v
Temporary Python relay in printer RAM
       | local TCP
       v
nginx :80 -> Fluidd / Moonraker :7125
~~~

The bridge runs on Windows. It launches a small in-memory Python relay through the existing printer Linux console. **No firmware flash, printer-side package installation or permanent printer file changes are required.**

## Real-world validation (2026-10-16)

Tests were performed on Windows (Python 3.11.9), an Artillery Sidewinder X4 Plus and its USB-C CH340 port.

| Function | Observed result |
| --- | --- |
| Printer Linux console through COM3 | Connected |
| Fluidd page through bridge | HTTP 200 and rendered in OrcaSlicer Device tab |
| Moonraker HTTP API | /server/info returned Klipper ready |
| WebSocket | 101 upgrade plus successful JSON-RPC server.info reply |
| Temperatures, console, bed mesh | Visible inside OrcaSlicer |
| OrcaSlicer print-host test | Passed |
| Sliced G-code upload / print start | User confirmed correct job loaded and heating began |
| Full-print completion, long-run reliability and reconnect | **Not independently verified** |

This USB approach does not require the PC and printer to share a network.

## Requirements

- Windows and Python **3.9 or newer**, with pyserial.
- Artillery Sidewinder X4 Plus with a CH340 Linux console accessible from its USB-C port.
- Stock printer Linux with Python 3 and nginx serving Fluidd and proxying Moonraker. Other firmware and models are not guaranteed compatible.
- Linux login credentials if the printer's console is not already logged in.

## Quick start on Windows

1. Connect a **PC USB port to the printer's USB-C device port**. Never use an unsafe USB-A-to-USB-A cable to connect two host ports.
2. In Device Manager, find the real USB-SERIAL CH340 (COMx) port. **Close PuTTY and HW VSP3** before starting: COM3 can only be used by one application at a time.
3. Install the PC dependency:

   ~~~powershell
   py -m pip install -r requirements.txt
   ~~~

4. Double-click **Lancer_X4_USB_Bridge.bat** (defaults to COM3 at 1500000 baud and local port 8181), or run:

   ~~~powershell
   py -u x4_usb_bridge.py --com COM4 --baud 1500000 --port 8181
   ~~~

5. Wait for the tunnel-active message, then visit:
   - API check: http://127.0.0.1:8181/server/info
   - Fluidd: http://127.0.0.1:8181/

6. In **OrcaSlicer → Physical Printer → Print Host upload** use:

   | Field | Value |
   | --- | --- |
   | Host Type | Octo/Klipper |
   | Hostname, IP or URL | 127.0.0.1:8181 |
   | Device UI | http://127.0.0.1:8181/ |
   | API Key / Password | Leave empty unless required by your Moonraker setup |

Click **Test** and open the **Device** tab.

Leave the bridge running during use. To stop it, press Ctrl+C in its console. Only one bridge may use COM3 at a time.

### Read-only diagnostics

When the bridge is stopped and COM3 is free:

~~~powershell
py x4_usb_bridge.py --com COM3 --probe-only
~~~

This checks the Linux console and internal nginx/Moonraker HTTP endpoints; it does **not** start the relay.

## Current limitations and safety

- Automatic reconnect, hard disconnect recovery, prolonged usage and high-volume throughput are not yet qualified.
- Disconnecting USB or stopping the bridge breaks the remote connection; it does **not** ensure that an already-started print will stop. Use printer hardware controls and supervise first prints.
- The Windows listener binds to **127.0.0.1 only**. Other programs **on the same PC** may still access Moonraker and issue real machine commands. Do not expose port 8181 to the LAN or Internet, or use on untrusted computers.
- Factory Linux login credentials should be changed where possible. The current prototype may try a manufacturer default when running non-interactively; never put actual passwords in issues or logs.
- Other printers, baud rates and Linux environments have not been tested.

## Offline tests

~~~powershell
py -m unittest discover -s tests -v
~~~

The unit tests check binary framing, fragmentation, CRC recovery and syntax; they never open COM3 or issue G-code.

## Contributions

See [CONTRIBUTING.md](CONTRIBUTING.md). We welcome documented compatibility and stability test reports, source improvements and safe reproducible tests.

## License

[MIT](LICENSE) © 2026 Wonderful Arcane. Independent community project, not affiliated with Artillery, OrcaSlicer, Fluidd or Moonraker.
