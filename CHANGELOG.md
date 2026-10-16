# Changelog

## 0.2.0-beta.4 — guarded USB reconnect watchdog (offline-validated)

- Detect a silently vanished CH340 even if the Windows worker process remains
  alive. Require USB absence for five seconds before gracefully restarting.
- Monitor valid framed UART replies via a low-noise worker heartbeat; after
  50 seconds of confirmed silence, restart the owned worker with bounded retry.
- Catch transient Windows serial-enumeration exceptions so the Tkinter event
  loop always continues.
- Stop only this GUI's worker process tree (including PyInstaller child), never
  another serial application or printer firmware process.
- Refuse an automatically reassigned COM port with mismatched USB identity;
  explain why the user must manually confirm a changed location.
- Avoid sending STOP to stdin while the worker might still be reading a Linux
  password.
- Added offline tests for serial disappearance, short device blips, worker
  stalls, unknown USB identities, and process-tree cleanup. The physical
  USB unplug/replug behaviour of beta.4 is **not yet validated on hardware**.
- No G-code compression or rewriting; the existing bidirectional ACK/CRC
  transport from beta.3 is unchanged.

## 0.2.0-beta.3 — bidirectional reliable USB transport

- Added CRC-checked serial sequencing, ACKs, and automatic retransmissions
  for **both** PC-to-printer uploads and printer-to-PC responses.
- Kept OrcaSlicer HTTP/WebSocket/G-code payload bytes completely unchanged:
  transport frames differ, but there is **no G-code compression or rewriting**.
- Isolated CH340 receive thread from frame decoding and response delivery.
- Recover automatically from a temporarily unavailable Linux serial shell after
  USB re-enumeration. An orphaned RAM relay can occupy the tty until its
  heartbeat times out; retrying with bounded backoff avoids falsely treating
  this as a rejected Linux login. Stop retries only on actual bad credentials.
- A supervised simulated Windows worker crash on the real X4 Plus recovered
  without manual restart after two transient console-not-ready failures.
  Physical cable unplug/replug recovery with this correction remains to be
  independently reconfirmed before a stable release.
- Real Artillery Sidewinder X4 Plus tests (printer idle, no print started):
  512 KiB, 1.25 MiB and 2 MiB files uploaded successfully through USB,
  read back with matching SHA-256, and removed. The 2 MiB upload averaged
  approximately 82.6 KiB/s to the Moonraker response.
- A 5 MiB HTTP download completed without missing bytes, even after CRC
  resynchronizations. Reliability remains experimental until long-running
  monitoring and unplug/replug tests are performed.
- Retained the v0.1 working baseline in the separate `main` branch.

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
