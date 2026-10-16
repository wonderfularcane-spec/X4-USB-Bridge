"""Offline tests for Linux tty readiness after CH340 unplug/replug."""
import unittest
from unittest.mock import patch

import x4_usb_bridge as bridge
from x4_usb_bridge_gui import BridgeWindow


class FakeSerial:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class FakeButton:
    def __init__(self):
        self.calls = []

    def configure(self, **options):
        self.calls.append(options)


def fake_window():
    window = BridgeWindow.__new__(BridgeWindow)
    window.proc = object()
    window.active = True
    window.status = FakeVar()
    window.start_button = FakeButton()
    window.stop_button = FakeButton()
    window.retries = 3
    window.log = lambda line: None
    return window


class LinuxConsoleRecoveryTests(unittest.TestCase):
    def test_missing_shell_is_retryable_not_permanent_login_failure(self):
        port = FakeSerial()
        with patch.object(bridge, "read_for", return_value=b""), patch.object(
            bridge, "shell_probe", return_value=(False, b"")
        ):
            with self.assertRaisesRegex(RuntimeError, "CONSOLE_NOT_READY"):
                bridge.login_if_needed(port, "mks")
        self.assertEqual(port.writes[0], b"\x15\r")
        self.assertEqual(port.writes.count(b"\x15\r"), 1)

    def test_gui_keeps_retry_active_for_occupied_linux_tty(self):
        win = fake_window()
        win.process_line(win.proc, "[ERREUR] CONSOLE_NOT_READY: Linux tty busy")
        self.assertTrue(win.active)
        self.assertEqual(win.status.value, "Linux console busy — will retry")
        self.assertEqual(win.start_button.calls, [])
        self.assertEqual(win.retries, 3)

    def test_gui_stops_on_real_bad_credentials(self):
        win = fake_window()
        win.process_line(win.proc, "[ERREUR] AUTH_FAILED: Linux login rejected")
        self.assertFalse(win.active)
        self.assertIn({"state": "normal"}, win.start_button.calls)
        self.assertEqual(win.status.value, "Linux login failed — check credentials")


if __name__ == "__main__":
    unittest.main()
