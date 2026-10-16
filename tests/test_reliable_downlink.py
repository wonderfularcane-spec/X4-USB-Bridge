"""Pure in-memory tests for reliable printer-to-PC UART frames.

A fake serial port substitutes for COM3. Nothing connects to a printer.
"""
import queue
import threading
import time
import unittest
from types import SimpleNamespace

import x4_usb_bridge as bridge


class FakeSerial:
    def __init__(self):
        self.incoming = queue.Queue()
        self.writes = []
        self.lock = threading.Lock()
        self.closed = False

    @property
    def in_waiting(self):
        return self.incoming.qsize()

    def read(self, count):
        try:
            return self.incoming.get(timeout=0.025)
        except queue.Empty:
            return b""

    def write(self, data):
        with self.lock:
            self.writes.append(bytes(data))
        return len(data)

    def inject(self, data):
        self.incoming.put(data)

    def close(self):
        self.closed = True

    def sent_frames(self):
        with self.lock:
            wire = b"".join(self.writes)
        return bridge.FrameParser().feed(wire)


class DownlinkReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.port = FakeSerial()
        self.session = bridge.Session(self.port)
        self.handler = SimpleNamespace(incoming=queue.Queue())
        with self.session.clients_lock:
            self.session.clients[53] = self.handler

    def tearDown(self):
        self.session.close()

    def inject(self, seq, data):
        packet = bridge.pack_frame(
            bridge.RDATA, 53, bridge.CRC.pack(seq) + data
        )
        self.port.inject(packet)
        return packet

    def test_received_payload_waits_for_socket_delivery_before_ack(self):
        original = b"G1 X10 Y20\n" * 120
        self.inject(1, original)
        typ, body = self.handler.incoming.get(timeout=1)
        self.assertEqual(typ, bridge.RDATA)
        self.assertEqual(body[4:], original)
        self.assertEqual(self.port.sent_frames(), [])
        self.session.ack_downlink(1)
        self.assertIn(
            (bridge.ACK, 0, bridge.CRC.pack(1)),
            self.port.sent_frames(),
        )

    def test_retransmitted_packet_is_not_delivered_twice(self):
        self.inject(1, b"unchanged")
        item = self.handler.incoming.get(timeout=1)
        self.assertEqual(item[1][4:], b"unchanged")
        self.inject(1, b"unchanged")
        with self.assertRaises(queue.Empty):
            self.handler.incoming.get(timeout=0.1)
        self.session.ack_downlink(1)
        self.inject(1, b"unchanged")
        time.sleep(0.08)
        self.assertEqual(self.handler.incoming.qsize(), 0)
        self.assertGreaterEqual(
            self.port.sent_frames().count((bridge.ACK, 0, bridge.CRC.pack(1))), 2
        )

    def test_crc_failure_requires_retransmission(self):
        packet = bytearray(
            bridge.pack_frame(bridge.RDATA, 53, bridge.CRC.pack(1) + b"binary")
        )
        packet[-1] ^= 0xFF
        self.port.inject(bytes(packet))
        with self.assertRaises(queue.Empty):
            self.handler.incoming.get(timeout=0.12)
        self.assertEqual(self.port.sent_frames(), [])
        self.inject(1, b"binary")
        item = self.handler.incoming.get(timeout=1)
        self.assertEqual(item[1][4:], b"binary")
        self.session.ack_downlink(1)
        self.assertGreater(self.session.parser.dropped, 0)

    def test_out_of_order_frame_does_not_skip_bytes(self):
        self.inject(2, b"second")
        with self.assertRaises(queue.Empty):
            self.handler.incoming.get(timeout=0.1)
        self.inject(1, b"first")
        self.assertEqual(self.handler.incoming.get(timeout=1)[1][4:], b"first")
        self.session.ack_downlink(1)
        self.inject(2, b"second")
        self.assertEqual(self.handler.incoming.get(timeout=1)[1][4:], b"second")
        self.session.ack_downlink(2)
        self.assertEqual(self.session.rx_acked_seq, 2)

    def test_printer_relay_source_compiles(self):
        compile(bridge.REMOTE_SOURCE, "<ephemeral-linux-relay>", "exec")
        self.assertIn("X4_USB_BRIDGE_READY_V1", bridge.REMOTE_SOURCE)
        self.assertIn("RDATA", bridge.REMOTE_SOURCE)


if __name__ == "__main__":
    unittest.main()
