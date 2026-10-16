"""Windows GUI watchdog tests; entirely simulated, never open COM3."""
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import x4_usb_bridge_gui as gui
from x4_bridge_ports import port_identity, reconnect_mismatch


def ch340(device="COM3", location="1-1", vid=0x1A86, pid=0x7523):
    return SimpleNamespace(
        device=device, location=location, serial_number="",
        vid=vid, pid=pid, description="USB-SERIAL CH340"
    )


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakePipe:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        pass


class FakeWorker:
    def __init__(self):
        self.pid = 12345
        self.returncode = None
        self.stdin = FakePipe()
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


def fake_window(proc=None):
    win = gui.BridgeWindow.__new__(gui.BridgeWindow)
    win.proc = proc
    win.root = Mock()
    win.closing = False
    win.active = True
    win.connected_at = 1 if proc is not None else None
    win.last_worker_health = 95 if proc is not None else None
    win.selected = "COM3"
    win.identity = port_identity(ch340())
    win.last_usb_poll = 0
    win.usb_missing_since = None
    win.last_usb_error_log = 0
    win.last_port_warning = None
    win.restart_requested_at = None
    win.retries = 0
    win.retry_at = 0
    win.stopping_at = 0
    win.lines = queue.Queue()
    win.status = FakeVar()
    win.start_button = Mock()
    win.stop_button = Mock()
    win.logs = []
    win.log = win.logs.append
    win.launch = Mock()
    return win


class GUIReconnectWatchdogTests(unittest.TestCase):
    def test_temporary_windows_enumeration_error_never_stops_event_loop(self):
        win = fake_window(None)
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=50), patch(
            "x4_usb_bridge_gui.list_ports.comports",
            side_effect=OSError("USB setup API temporarily busy")
        ):
            win.tick()
        self.assertTrue(win.active)
        self.assertIsNone(win.proc)
        self.assertEqual(win.status.get(), "USB enumeration unavailable — retrying")
        self.assertTrue(win.root.after.called)
        self.assertGreater(win.retry_at, 50)

    def test_single_short_usb_disappearance_does_not_restart_active_worker(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win._list_usb_ports = lambda: []
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=100):
            win.tick()
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=103):
            win.tick()
        self.assertEqual(win.usb_missing_since, 100)
        self.assertIsNone(win.restart_requested_at)
        self.assertEqual(worker.stdin.lines, [])
        self.assertFalse(worker.terminated)

    def test_sustained_unplug_stops_only_this_worker_then_auto_relaunches(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win._list_usb_ports = lambda: []
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=100):
            win.tick()
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=106):
            win.tick()
        self.assertEqual(worker.stdin.lines, ["STOP\n"])
        self.assertEqual(win.restart_requested_at, 106)
        self.assertTrue(win.active)
        worker.returncode = 0
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=107):
            win.tick()
        self.assertIsNone(win.proc)
        self.assertEqual(win.retries, 1)
        win._list_usb_ports = lambda: [ch340()]
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=112):
            win.tick()
        win.launch.assert_called_once_with("COM3")
        self.assertTrue(win.active)

    def test_reconnect_resumes_without_killing_if_port_returns(self):
        worker = FakeWorker()
        win = fake_window(worker)
        seen = [[], [ch340()]]
        win._list_usb_ports = lambda: seen.pop(0)
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=100):
            win.tick()
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=103):
            win.tick()
        self.assertIsNone(win.usb_missing_since)
        self.assertEqual(worker.stdin.lines, [])
        self.assertTrue(win.active)

    def test_changed_usb_location_is_not_silently_trusted(self):
        old = port_identity(ch340(location="1-1"))
        candidate = ch340(location="3-4")
        self.assertEqual(reconnect_mismatch([candidate], old),
                         "USB location changed; press Stop / Start to confirm")
        self.assertIsNone(reconnect_mismatch([ch340()], old))
        win = fake_window(None)
        win.identity = old
        win._list_usb_ports = lambda: [candidate]
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=100):
            win.tick()
        self.assertEqual(win.launch.call_count, 0)
        self.assertIn("USB location changed", win.status.get())

    def test_different_usb_vid_pid_does_not_get_connected(self):
        identity = port_identity(ch340())
        unknown = ch340(vid=0x1234, pid=0x5678)
        self.assertEqual(
            reconnect_mismatch([unknown], identity),
            "COM port now belongs to a different USB device",
        )

    def test_health_heartbeats_keep_live_worker_running(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win._list_usb_ports = lambda: [ch340()]
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=101):
            win.process_line(worker, "[X4_HEALTH_OK]")
        self.assertEqual(win.logs, [])
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=148):
            win.tick()
        self.assertTrue(win.active)
        self.assertEqual(worker.stdin.lines, [])
        self.assertIsNone(win.restart_requested_at)

    def test_stalled_worker_restarts_when_serial_device_still_present(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win._list_usb_ports = lambda: [ch340()]
        win.last_worker_health = 1
        # COM3 is visible, but the process has not received valid serial
        # frames for >50 sec. The UI must not stay "Connected" forever.
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=100):
            win.tick()
        self.assertEqual(worker.stdin.lines, ["STOP\n"])
        self.assertEqual(win.restart_requested_at, 100)

    def test_enumeration_error_during_active_session_keeps_polling(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win.last_worker_health = 100
        with patch("x4_usb_bridge_gui.time.monotonic", return_value=101), patch(
            "x4_usb_bridge_gui.list_ports.comports",
            side_effect=OSError("Windows re-enumeration pending"),
        ):
            win.tick()
        self.assertTrue(win.root.after.called)
        self.assertIsNone(win.usb_missing_since)
        self.assertEqual(worker.stdin.lines, [])

    def test_stop_during_login_must_not_submit_stop_as_password(self):
        worker = FakeWorker()
        win = fake_window(worker)
        win.connected_at = None
        with patch("x4_usb_bridge_gui.terminate_owned_worker") as terminate:
            win.stop(ask=False)
        terminate.assert_called_once_with(worker)
        self.assertEqual(worker.stdin.lines, [])

    def test_tree_termination_targets_only_owned_pid(self):
        worker = FakeWorker()
        with patch("x4_usb_bridge_gui.os.name", "nt"), patch(
            "x4_usb_bridge_gui.subprocess.run",
            return_value=SimpleNamespace(returncode=0)
        ) as invoked:
            gui.terminate_owned_worker(worker)
        self.assertEqual(
            invoked.call_args.args[0],
            ["taskkill", "/T", "/F", "/PID", str(worker.pid)],
        )


class WorkerHealthTests(unittest.TestCase):
    def test_health_signal_requires_real_recent_uart_reply(self):
        import x4_usb_bridge as bridge

        worker = bridge.Session.__new__(bridge.Session)
        worker.stop = Mock()
        worker.stop.wait.side_effect = [False, True]
        worker.send = Mock()
        worker.pong = 100
        worker.last_rx = 100
        worker.have_rx = True
        worker.report_health = True
        with patch("x4_usb_bridge.time.monotonic", return_value=105), patch(
            "builtins.print"
        ) as printed:
            worker.heartbeat()
        worker.send.assert_called_once_with(bridge.PING)
        printed.assert_called_once_with("[X4_HEALTH_OK]", flush=True)

    def test_worker_does_not_report_unconfirmed_serial_health(self):
        import x4_usb_bridge as bridge

        worker = bridge.Session.__new__(bridge.Session)
        worker.stop = Mock()
        worker.stop.wait.side_effect = [False, True]
        worker.send = Mock()
        worker.pong = 100
        worker.last_rx = 100
        worker.have_rx = False
        worker.report_health = True
        with patch("x4_usb_bridge.time.monotonic", return_value=105), patch(
            "builtins.print"
        ) as printed:
            worker.heartbeat()
        printed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
