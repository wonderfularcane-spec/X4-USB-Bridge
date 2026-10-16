"""Pure offline packaging checks: never access an actual printer or USB port."""
import tempfile
import unittest
from pathlib import Path

from x4_usb_bridge_gui import bridge_command, http_port_in_use


class PackagingTests(unittest.TestCase):
    def test_python_source_command(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "x4_usb_bridge.py").write_text("# offline fixture\n")
            cmd = bridge_command(
                "COM7", 1500000, 8181, frozen=False,
                python="python-example.exe", folder=temp,
            )
            self.assertEqual(cmd[0], "python-example.exe")
            self.assertEqual(cmd[1], "-u")
            self.assertEqual(cmd[-7:], [
                "--com", "COM7", "--baud", "1500000", "--port", "8181",
                "--control-stdin",
            ])

    def test_bundle_command_uses_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "X4_USB_Bridge_Worker.exe").write_bytes(b"offline-test")
            cmd = bridge_command("COM3", 1500000, 8181, frozen=True, folder=temp)
            self.assertTrue(cmd[0].endswith("X4_USB_Bridge_Worker.exe"))
            self.assertEqual(cmd[1], "--com")
            self.assertNotIn("-u", cmd)

    def test_bundle_must_have_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                bridge_command("COM3", 1500000, 8181, frozen=True, folder=temp)

    def test_local_http_port_check_is_non_mutating(self):
        # Port zero is not a valid production HTTP server; this remains offline.
        self.assertFalse(http_port_in_use(0))


if __name__ == "__main__":
    unittest.main()
