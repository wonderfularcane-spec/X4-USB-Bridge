# Changelog

## 0.2.0-beta.2 — experimental Windows preview (GitHub Actions dependency fix)

- Portable Windows GUI with local port setup, serial-port selection and log display.
- A separate CLI bridge worker to keep the GUI responsive.
- Controlled retry on lost CH340 serial connections, with bounded backoff.
- Checks to avoid silently reconnecting to an unrelated USB device.
- Clean bridge shutdown on GUI Stop.
- Manual login prompt for non-interactive bridge sessions, instead of silently using a factory password.
- Improved UART exit handling so the worker can be restarted after a disconnect.
- Transparent TCP request-end handling: when a client finishes writing,
  keep the upstream nginx socket open until it replies. Forwarding a TCP FIN
  to nginx prematurely can make it drop its HTTP response (curl 52 regression).
  No new USB frame types were added; the v0.1 wire format is preserved.
- Higher-throughput UART writes: batch eight unchanged framed chunks into one
  bounded pyserial write; do not compress or rewrite any G-code/HTTP payloads.
- Split USB timing from HTTP response waiting in logs, so apparent slow uploads
  can be diagnosed without confusing serialization with Moonraker processing.
- If the upstream connection closes before delivering a response, return a
  clear HTTP 502 rather than an empty reply (curl 52). Use graceful TCP
  write-side shutdown on Windows to avoid hiding the error with a socket reset.
  Existing HTTP errors such as 413 remain unchanged, as do interim 100 Continue replies.
- These measurements do **not** prove Moonraker saved a file.
- Offline protocol, packaging, upload-integrity and reconnect tests.
- Windows portable bundle with two executables; **no Python installation required** for end users.

**Experimental:** the USB disconnect/reconnect recovery has passed offline tests but
has not yet passed an on-printer unplug/replug test. Keep v0.1 as fallback.

## 0.1.0 — initial prototype

- Serial Linux-console to HTTP / Moonraker / Fluidd / WebSocket tunnel.
- Artillery Sidewinder X4 Plus: one fully completed successful print reported
  by the original tester through OrcaSlicer using the USB-C connection.
