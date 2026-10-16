"""No-printer tests for COM device matching, retry policy and server shutdown."""
import threading
import unittest
from types import SimpleNamespace

from x4_bridge_ports import (
    is_ch340, is_same_device, port_identity, retry_seconds, select_com_port,
)
from x4_usb_bridge import serve_until_stopped


def serial_port(name, vid=0x1A86, pid=0x7523, location="2-1", serial_number=None,
                description="USB-SERIAL CH340"):
    return SimpleNamespace(device=name, vid=vid, pid=pid, location=location,
                           serial_number=serial_number, description=description)


class PortSelectionTests(unittest.TestCase):
    def test_one_ch340_can_be_detected(self):
        printer = serial_port("COM3")
        bluetooth = serial_port("COM6", 12, 13, None, description="Bluetooth")
        self.assertTrue(is_ch340(printer))
        self.assertIs(select_com_port([bluetooth, printer]), printer)

    def test_ambiguous_ch340_requires_selection(self):
        a = serial_port("COM3", location="1-1")
        b = serial_port("COM4", location="1-2")
        self.assertIsNone(select_com_port([a, b]))
        self.assertIs(select_com_port([a, b], preferred="COM4"), b)

    def test_replug_same_usb_location_can_change_com_number(self):
        original = serial_port("COM3", location="1-4")
        replugged = serial_port("COM8", location="1-4")
        ident = port_identity(original)
        self.assertTrue(is_same_device(replugged, ident))
        self.assertIs(select_com_port([replugged], identity=ident), replugged)

    def test_different_usb_device_cannot_be_picked_for_reconnect(self):
        original = serial_port("COM3", location="1-4")
        other = serial_port("COM8", location="1-5")
        self.assertIsNone(select_com_port([other], identity=port_identity(original)))

    def test_anonymous_device_never_switches_com_number(self):
        original = serial_port("COM3", location=None)
        other = serial_port("COM8", location=None)
        self.assertIsNone(select_com_port([other], identity=port_identity(original)))

    def test_explicit_com_missing_never_falls_back_to_other_device(self):
        other = serial_port("COM8")
        self.assertIsNone(select_com_port([other], preferred="COM3"))

    def test_retry_backoff_is_bounded(self):
        self.assertEqual([retry_seconds(n) for n in range(1, 7)],
                         [4, 8, 16, 30, 30, 30])


class ServerLoopTests(unittest.TestCase):
    def test_exits_when_link_goes_down(self):
        bridge = SimpleNamespace(stop=threading.Event())

        class FakeServer:
            timeout = None
            count = 0

            def handle_request(self):
                self.count += 1
                if self.count >= 4:
                    bridge.stop.set()

        server = FakeServer()
        serve_until_stopped(server, bridge)
        self.assertEqual(server.count, 4)
        self.assertLessEqual(server.timeout, 1)

    def test_gui_stop_is_honored(self):
        bridge = SimpleNamespace(stop=threading.Event())
        control_stop = threading.Event()
        control_stop.set()

        class FakeServer:
            timeout = None
            def handle_request(self):
                raise AssertionError("Should not accept connections after STOP")

        serve_until_stopped(FakeServer(), bridge, control_stop)


if __name__ == "__main__":
    unittest.main()
