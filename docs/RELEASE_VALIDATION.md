# Validation checklist before promoting v0.2

**Do not test on a heating or printing machine.** The v0.1 source on `main` remains the known-working baseline. The v0.2 Windows executable is experimental.

## Automated tests (no printer access)

Run `py -m unittest discover -s tests -v`. Required checks:

- Full HTTP and G-code payload bytes remain unchanged. Beta.3 serial transport frames add sequence numbers and CRC/ACK retransmission in both directions; the original v0.1 framing remains available on `main`.
- Arbitrary binary HTTP bodies remain unchanged, including a 2 MiB simulated upload.
- `Expect: 100-continue` and `201 Created` delivered through the bridge.
- Rejected uploads propagate HTTP `413`; lost upstream connections produce `502` rather than empty responses.
- Failed COM connection retries cannot silently switch to another USB device.
- Window Close/Stop disables auto-reconnect and shuts down its own child process.
- The Windows packaged GUI and worker both pass non-USB startup checks.

## Physical acceptance tests (only when printer is idle)

These are **not** automated because they require an operator, real electronics and knowledge of current printer state.

1. Confirm printer idle and both heater targets set to 0 °C. Finish/cancel any live job through the normal printer controls; never kill a printing job by unplugging USB.
2. Stop previous v0.1 bridge normally, confirm its process is no longer holding the CH340 COM port.
3. Start v0.2 GUI, select the CH340, connect. Check the local `/server/info` endpoint, Fluidd and WebSocket monitoring.
4. Submit a small test G-code file with `print=false`. Read the file back via Moonraker and verify its SHA-256, then delete it.
5. Try gradually larger uploads using a **slicer-generated** test file, with `print=false`: 512 KiB, 1 MiB, 2 MiB, and representative real-world size. Record HTTP status, USB bytes/sec and Moonraker response time. Do not alter the file contents.
6. Confirm OrcaSlicer receives usable error responses on rejected files, with no changes to print-host configuration.
7. Unplug and replug USB **while the printer is idle and cooled down**. The old Linux RAM relay may take about 30 seconds to time out, so allow at least 90 seconds for bounded retries. Verify that the bridge does not disable reconnect merely because the Linux console is busy; confirm the original CH340 reappears, Fluidd and `/server/info` work, and unrelated serial devices are never selected. This exact physical test is still pending after the newest fix.
8. Monitor a full idle period and finally supervise one representative print only after all prior checks pass.

## Rollback

- Stop the v0.2 GUI/worker without affecting the printer's own operation.
- Reconnect to the existing v0.1 program from the unchanged `main` checkout.
- Do not replace printer firmware, reboot mid-print or modify nginx settings in order to pass these checks.

## Report

Please share only printer model, firmware revision, COM baud, file sizes, checksums, HTTP status codes, timings, and sanitized error logs. Do **not** publish Linux passwords, Moonraker API keys, private IP addresses or identifiable machine logs.

An HTTP `502` indicates the bridge did not obtain a usable upstream response; it does **not** identify the exact cause. An HTTP `413` suggests the request was rejected for size. This bridge does not modify any HTTP upload or G-code body.
