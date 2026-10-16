"""Offline sliding-window UART research; NOT USED by the active USB bridge.

This intentionally does not import pyserial, open COM ports, connect to
Moonraker, or modify application data. It compares stop-and-wait (window=1)
with selective-repeat retransmission (window=4) using a simulated full-duplex
1.5 Mbaud link. Numbers from this model are NOT real printer benchmarks.
"""
import heapq
import itertools
import random


SERIAL_BYTES_PER_SEC = 1500000 / 10
FRAME_OVERHEAD = 17   # X4B1 header and CRC (13 + 4)
ACK_WIRE_BYTES = 21   # X4B1 header and CRC + four-byte sequence number


class SelectiveRepeatSender:
    def __init__(self, payload, window=4, block_size=2044, timeout=0.16,
                 max_retries=16):
        if not 1 <= window <= 32:
            raise ValueError("window size must be 1..32")
        if not 1 <= block_size <= 2044:
            raise ValueError("block_size must be 1..2044")
        self.payload = memoryview(payload)
        self.window = window
        self.block_size = block_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.offset = 0
        self.next_seq = 1
        self.in_flight = {}  # seq -> [bytes, last_scheduled_at, transmissions]
        self.retries = 0

    @property
    def done(self):
        return self.offset == len(self.payload) and not self.in_flight

    def acknowledge(self, seq):
        return self.in_flight.pop(seq, None) is not None

    def produce(self, now):
        """Return (seq, bytes) for timed-out frames and new window capacity."""
        outgoing = []
        for seq, state in list(self.in_flight.items()):
            content, sent_at, attempts = state
            if now + 1e-9 >= sent_at + self.timeout:
                if attempts >= self.max_retries:
                    raise TimeoutError("Loss of ACK/data for serial frame %d" % seq)
                state[1] = now
                state[2] += 1
                self.retries += 1
                outgoing.append((seq, content))
        # A sliding window is anchored to the oldest unacknowledged seq,
        # NOT merely the number of unacknowledged frames. Otherwise early
        # ACKs could advance past a lost packet and overrun receiver capacity.
        while (self.offset < len(self.payload)
               and (not self.in_flight
                    or self.next_seq - min(self.in_flight) < self.window)):
            content = bytes(self.payload[self.offset:self.offset+self.block_size])
            seq = self.next_seq
            self.next_seq += 1
            self.offset += len(content)
            self.in_flight[seq] = [content, now, 1]
            outgoing.append((seq, content))
        return outgoing

    def scheduled_on_wire(self, seq, when):
        """Start the timeout only after this frame finishes serializing."""
        state = self.in_flight.get(seq)
        if state is not None:
            state[1] = when

    def next_deadline(self):
        return min((state[1] + self.timeout for state in self.in_flight.values()),
                   default=None)


class SelectiveRepeatReceiver:
    def __init__(self, window=4):
        self.window = window
        self.next_seq = 1
        self.buffer = {}
        self.output = bytearray()

    def receive(self, seq, content):
        """Return ACK seq for valid/duplicate packets, never duplicate data."""
        if seq < self.next_seq:
            return seq
        if seq >= self.next_seq + self.window:
            return None
        self.buffer.setdefault(seq, bytes(content))
        while self.next_seq in self.buffer:
            self.output.extend(self.buffer.pop(self.next_seq))
            self.next_seq += 1
        return seq


def simulate(payload, *, window=4, drop_data=0.0, drop_ack=0.0,
             seed=42, host_delay=0.009, max_simulated_seconds=120):
    """Send bytes over a simulated UART and return integrity/speed statistics.

    The outgoing/ACK wires are independently serialized as a full-duplex port.
    Corrupted CRC data frames are modeled as discarded data, never delivered.
    """
    rng = random.Random(seed)
    sender = SelectiveRepeatSender(payload, window=window)
    receiver = SelectiveRepeatReceiver(window=window)
    events = []  # (delivery_time, stable_counter, kind, seq, payload)
    order = itertools.count()
    now = 0.0
    data_link_free = 0.0
    ack_link_free = 0.0
    data_wire_bytes = 0
    ack_wire_bytes = 0

    while not sender.done:
        for seq, content in sender.produce(now):
            started = max(now, data_link_free)
            data_link_free = started + (len(content)+FRAME_OVERHEAD)/SERIAL_BYTES_PER_SEC
            sender.scheduled_on_wire(seq, data_link_free)
            data_wire_bytes += len(content) + FRAME_OVERHEAD
            heapq.heappush(events,
                           (data_link_free+host_delay/2, next(order),
                            "data", seq, content))
        if not events:
            next_timeout = sender.next_deadline()
            if next_timeout is None:
                raise RuntimeError("No UART packets or pending ACKs")
            now = max(now+0.000001, next_timeout)
            continue
        next_delivery = events[0][0]
        deadline = sender.next_deadline()
        if deadline is not None and deadline + 1e-9 < next_delivery:
            now = max(now+0.000001, deadline)
            continue
        now, _, kind, seq, content = heapq.heappop(events)
        if now > max_simulated_seconds:
            raise TimeoutError("Simulated duration exceeded safety bound")
        if kind == "data":
            if rng.random() < drop_data:
                continue
            ack_seq = receiver.receive(seq, content)
            if ack_seq is None:
                continue
            started = max(now, ack_link_free)
            ack_link_free = started + ACK_WIRE_BYTES/SERIAL_BYTES_PER_SEC
            ack_wire_bytes += ACK_WIRE_BYTES
            heapq.heappush(events,
                           (ack_link_free + host_delay/2, next(order),
                            "ack", ack_seq, b""))
        elif rng.random() >= drop_ack:
            sender.acknowledge(seq)

    received = bytes(receiver.output)
    return {
        "identical": received == payload,
        "payload_bytes": len(payload),
        "seconds": now,
        "throughput_KiB_s": len(payload)/1024/max(now, 0.00001),
        "data_wire_bytes": data_wire_bytes,
        "ack_wire_bytes": ack_wire_bytes,
        "retries": sender.retries,
        "window": window,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Offline serial window lab")
    parser.add_argument("--bytes", type=int, default=2*1024*1024)
    parser.add_argument("--drop", type=float, default=0.015)
    args = parser.parse_args()
    if args.bytes < 1 or args.bytes > 16*1024*1024:
        parser.error("Allowed sample size 1 byte to 16 MiB")
    if not 0 <= args.drop <= 0.2:
        parser.error("Drop probability must be 0..0.2")
    sample = bytes(range(256)) * (args.bytes//256) + bytes(range(args.bytes % 256))
    for win in (1, 2, 4, 8):
        result = simulate(sample, window=win, drop_data=args.drop,
                          drop_ack=args.drop/2)
        print("window",win,"throughput_model",round(result["throughput_KiB_s"],1),
              "KiB/s","retries",result["retries"],"identical",result["identical"])
