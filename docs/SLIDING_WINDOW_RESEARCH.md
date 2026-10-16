# Sliding-window UART reliability lab (offline research only)

This work is **not included in the beta.4 executable** and must not be mistaken for a change to OrcaSlicer, the printer firmware, or transmitted G-code. No real serial-port access is made by this laboratory.

## Why investigate a window?

The tested Artillery Sidewinder X4 Plus uses a CH340 serial console at
1,500,000 baud. With 8N1 framing, the ideal physical limit is about
150,000 bytes/sec (**146.5 KiB/sec**), before tunnel overhead.

The current tested beta.3 transport uses one 2-KiB CRC-checked data frame,
then waits for an acknowledgement before sending the next frame. This
protects content but may leave the serial transmit path idle while the ACK
travels back to the PC.

The `tools/offline_window_lab.py` prototype models a **selective-repeat**
window that transmits up to 2, 4 or 8 frames concurrently. It retransmits
individual timed-out packets and never delivers out-of-order or duplicated
application bytes.

## Simulation methodology

- 1,500,000 baud, 8N1, full-duplex serial links for data and ACK.
- Packet payload: 2,044 bytes; each frame adds protocol overhead.
- Synthetic round-trip software delay: 9 ms, for illustration only.
- Seeded random data-frame and ACK loss; corruption is represented as a
  dropped frame because the actual transport CRC rejects damaged frames.
- Strict upper bounds on in-flight data and retries.
- The *actual* CPU cost, USB driver buffering, Windows scheduling jitter,
  Linux socket buffering, nginx behaviour, and Moonraker metadata handling
  are **not modeled**.

Run without connecting to any printer:

```powershell
py -m tools.offline_window_lab --bytes 524288 --drop 0.015
py -m unittest discover -s tests -v
```

An illustrative seeded 512-KiB simulation with 1.5% dropped data frames
and 0.75% dropped ACKs produced approximately these **modeled** payload
rates, *not physical measurements*:

| In-flight window | Simulated KiB/s |
| --- | ---: |
| 1 (existing strategy) | 67 |
| 2 | 98 |
| 4 | 103 |
| 8 | 118 |

Results vary with delay and error assumptions. A window of 8 can look
attractive in this model but may overwhelm the actual CH340 or Linux
relay and trigger more CRC errors. **There is no verified performance
improvement until a future hardware test demonstrates it.**

## Acceptance gates before integration

1. Full byte-for-byte equality of arbitrary HTTP/WebSocket/G-code payloads,
   including stream boundaries and multiple simultaneous TCP clients.
2. CRC-detected corruption must never release damaged data to Moonraker.
3. Duplicate ACKs and retransmissions must not duplicate application data.
4. A lost ACK must not advance the sender past unacknowledged bytes.
5. The receiver must never buffer more than the configured window permits.
6. A permanently disconnected cable must stop retries cleanly, with no
   uncontrolled endless buffering or CPU loop.
7. The sender must not starve Moonraker console/control traffic behind
   long upload bursts.
8. Only after all offline checks succeed should an operator test on a
   cold, idle printer using `print=false` and SHA-256 read-back.

The design expressly forbids G-code compression, transformations, and
OrcaSlicer modifications.
