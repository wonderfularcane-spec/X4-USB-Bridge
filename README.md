# X4 USB Bridge

**Control an Artillery Sidewinder X4 Plus from OrcaSlicer using its USB-C serial connection — without Wi-Fi, LAN access or printer firmware changes.**

> **Version status:** **v0.1** is the known-working Python prototype: the original tester reported a complete successful print on an X4 Plus. **v0.2.0-beta.3** adds a Windows GUI and a supervised reconnect mechanism, but physical USB unplug/replug is **not yet validated**. Do not assume v0.2 is production-ready.

## Windows v0.2 portable preview (no Python required)

1. Download the prerelease ZIP from [GitHub Releases](https://github.com/wonderfularcane-spec/X4-USB-Bridge/releases) (available to users with access to the repository).
2. Extract it somewhere writable on Windows. Keep **X4_USB_Bridge.exe** and **X4_USB_Bridge_Worker.exe** in the same folder.
3. Close PuTTY, HW VSP3, and any earlier bridge process that holds COM3. Ensure the printer is idle before replacing a working connection.
4. Open **X4_USB_Bridge.exe**, select the CH340 serial port (or auto-detect), then click **Start / auto-reconnect**.
5. Open Fluidd via the button, or use **127.0.0.1:8181** in OrcaSlicer as detailed below.
6. The Stop button disconnects remote monitoring. A running print can continue after disconnection; do not use Stop as an emergency stop.

The interface offers a status indicator, a log viewer, and recovery attempts after the serial link drops. It only reconnects to a matching USB device. Retries are rate-limited. If an old RAM-only printer relay still has the Linux tty in raw mode after a disconnect, the GUI waits and retries automatically rather than permanently disabling reconnection. A controlled worker-crash simulation has passed on an X4 Plus; a second physical unplug/replug test after this fix remains outstanding. The CLI version remains available for troubleshooting.

**Important:** the GUI must be running to manage reconnections. When it is closed, the link stops. A printer on an unexpected Linux login prompt may require manual credentials; the GUI prompts without storing them.

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

**Data integrity:** OrcaSlicer's HTTP requests, WebSocket messages and G-code bytes are forwarded unchanged. The beta.3 serial tunnel adds sequence numbers, CRC checks, acknowledgements and automatic retransmission **in both directions**. These are transport-only frames: it never compresses, rewrites or interprets G-code. The zlib library in the source is used only for temporary relay startup and CRC checksum calculation. The older v0.1 wire protocol is unchanged on the `main` branch.

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
| Sliced G-code upload / print start | Correct job loaded and heating began |
| Completed physical print | Original tester confirmed a successful complete print using v0.1 |
| Long-run reliability and USB unplug/replug recovery | **Not yet validated** |

This USB approach does not require the PC and printer to share a network.

## Requirements

- Windows x64. The portable v0.2 ZIP includes its own Python runtime and does **not** require a separate Python installation. For source-code CLI use Python **3.9 or newer** with pyserial.
- Artillery Sidewinder X4 Plus with a CH340 Linux console accessible from its USB-C port.
- Stock printer Linux with Python 3 and nginx serving Fluidd and proxying Moonraker. Other firmware and models are not guaranteed compatible.
- Linux login credentials if the printer's console is not already logged in.

## Source-code CLI quick start (v0.1 workflow)

Use this if you prefer Python rather than the portable graphical v0.2 preview.

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

- Automatic reconnect after physical USB unplug/replug and long-term print stability still require validation. In beta.3, actual 512 KiB, 1.25 MiB and 2 MiB test uploads completed over the USB bridge with Moonraker `print=false`, verified by SHA-256 read-back and removal. The measured upload throughput reached about 82.6 KiB/s for the 2 MiB test, **not a guarantee for all files or printers**. UART at 1,500,000 baud in 8N1 has an approximate maximum of 150 kB/s before protocol overhead.
- Disconnecting USB or stopping the bridge breaks the remote connection; it does **not** ensure that an already-started print will stop. Use printer hardware controls and supervise first prints.
- The Windows listener binds to **127.0.0.1 only**. Other programs **on the same PC** may still access Moonraker and issue real machine commands. Do not expose port 8181 to the LAN or Internet, or use on untrusted computers.
- Factory Linux login credentials should be changed where possible. The v0.2 GUI asks when the console requires login and does not store the value; avoid putting any credentials in issue reports or logs.
- Other printers, baud rates and Linux environments have not been tested.
- Uploads can fail for **printer-side HTTP limits** (for example nginx
  `client_max_body_size`) or during Moonraker metadata extraction. A 413
  response indicates a rejected HTTP request, not necessarily corrupt G-code.
  The actual nginx limit on every model/firmware is **not known**; this tool
  does not change printer configuration. If nginx closes before replying,
  v0.2 reports 502 rather than silently returning zero bytes.
- Timings in the GUI log distinguish **USB forwarding time** from **HTTP response
  latency**. Neither is proof of a completed upload; rely on Moonraker's
  success response and a checksum-verified read-back where applicable.

## Offline tests

~~~powershell
py -m unittest discover -s tests -v
~~~

The offline tests check framing, fragmentation, CRC recovery, port matching,
reconnect backoff, GUI command construction, TCP half-close, HTTP errors,
a 2 MiB `Expect: 100-continue` upload, byte-for-byte identity and shutdown.
They do not open COM3 or issue G-code.

### Build the portable Windows executables

On Windows x64 with Python 3.11 and an Internet connection for build dependencies:

~~~powershell
py tools/build_windows.py
~~~

The script creates an isolated build environment, generates both executables, tests their startup without accessing the printer, and packages them in `dist/X4-USB-Bridge-v0.2.0-beta.3-windows-x64.zip`. Build dependencies are pinned in `build-requirements.txt`.

See [CHANGELOG.md](CHANGELOG.md), [compatibility notes](docs/COMPATIBILITY.md), and the [hardware validation checklist](docs/RELEASE_VALIDATION.md). **Do not switch from the working v0.1 during an active print.**

## Contributions

See [CONTRIBUTING.md](CONTRIBUTING.md). We welcome documented compatibility and stability test reports, source improvements and safe reproducible tests.

## License

[MIT](LICENSE) © 2026 Wonderful Arcane. Independent community project, not affiliated with Artillery, OrcaSlicer, Fluidd or Moonraker.
