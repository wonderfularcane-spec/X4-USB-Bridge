# Printer compatibility and community test plan

A successful connection on one printer **does not** establish compatibility with all Klipper machines.

## Verified on hardware

| Printer | USB port | Host OS | Serial interface | Outcome |
| --- | --- | --- | --- | --- |
| Artillery Sidewinder X4 Plus | USB-C | Windows | CH340 Linux console, 1500000 baud | Moonraker API, WebSocket, Fluidd, OrcaSlicer device tab and one complete print confirmed by original tester |

## Unknown — volunteers welcome

| Printer family | Status | What needs checking |
| --- | --- | --- |
| Artillery Sidewinder X4 Pro, X4 Plus S1 | Untested | Is there a Linux login prompt on the USB-C port? Does nginx proxy Moonraker? |
| Other Artillery printers | Untested | Linux-console access via USB, Python 3 availability, correct baud |
| Other Klipper printers with an embedded Linux host | Untested | Physical USB serial console and HTTP proxy layout |
| Standard Marlin USB G-code serial printers | Not directly supported | USB is not a Linux terminal, so the current relay architecture cannot run |

## Minimum compatibility checklist

1. Verify the PC detects a physical USB serial device (not a COM port created by a virtual serial-port mapper).
2. Using a terminal, establish that the port leads to a Linux **shell**, not a G-code serial firmware console.
3. Confirm the printer already runs Python 3 and that its loopback HTTP endpoints provide Fluidd and Moonraker.
4. Use read-only diagnostics first. Avoid reinstalling firmware, flashing control boards or changing configurations.
5. Try the v0.1 or development bridge and check Moonraker /server/info, Fluidd and WebSockets.
6. Test sending a non-critical G-code file under supervision, then finish a supervised print.
7. Test interrupted USB connections only while the printer is idle and cooled down.

Please record model, firmware, Python version, baud rate, endpoint ports and outcomes, but **never include login credentials, API keys, serial numbers or private network data**.

## Honest scope

This bridge solves USB *console-to-Moonraker* access for a specific class of printers. It is not a universal OrcaSlicer USB driver, OctoPrint replacement or protocol adapter for bare USB G-code serial printers.
