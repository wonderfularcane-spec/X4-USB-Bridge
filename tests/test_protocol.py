"""Offline protocol tests: never open COM ports or connect to a printer."""

import unittest

import x4_usb_bridge as bridge


class FrameProtocolTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        payload = b"GET /server/info HTTP/1.1\r\nHost: localhost\r\n\r\n"
        packet = bridge.pack_frame(bridge.DATA, 42, payload)
        self.assertEqual(
            bridge.FrameParser().feed(packet),
            [(bridge.DATA, 42, payload)],
        )

    def test_fragmented_stream(self):
        payload = bytes(range(256))
        packet = bridge.pack_frame(bridge.DATA, 7, payload)
        parser = bridge.FrameParser()
        result = []
        for index in range(0, len(packet), 3):
            result.extend(parser.feed(packet[index:index + 3]))
        self.assertEqual(result, [(bridge.DATA, 7, payload)])

    def test_multiple_interleaved_channels(self):
        frames = [
            (bridge.OPEN, 1, b""),
            (bridge.DATA, 1, b"one"),
            (bridge.OPEN, 2, b""),
            (bridge.DATA, 2, b"two"),
            (bridge.CLOSE, 1, b""),
            (bridge.PING, 0, b""),
        ]
        stream = b"".join(bridge.pack_frame(*frame) for frame in frames)
        self.assertEqual(bridge.FrameParser().feed(stream), frames)

    def test_bad_crc_and_noise_are_ignored(self):
        good = bridge.pack_frame(bridge.DATA, 99, b"valid")
        bad = bytearray(bridge.pack_frame(bridge.DATA, 98, b"invalid"))
        bad[-1] ^= 0xFF
        parser = bridge.FrameParser()
        self.assertEqual(
            parser.feed(b"garbage" + bytes(bad) + good),
            [(bridge.DATA, 99, b"valid")],
        )
        self.assertGreater(parser.dropped, 0)

    def test_max_payload(self):
        payload = b"x" * bridge.MAX_PAYLOAD
        self.assertEqual(
            bridge.FrameParser().feed(bridge.pack_frame(bridge.DATA, 5, payload)),
            [(bridge.DATA, 5, payload)],
        )
        with self.assertRaises(ValueError):
            bridge.pack_frame(bridge.DATA, 5, payload + b"x")

    def test_printer_relay_syntax_only(self):
        compile(bridge.REMOTE_SOURCE, "<memory-only-printer-relay>", "exec")


if __name__ == "__main__":
    unittest.main()
