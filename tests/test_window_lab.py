"""Offline-only selective-repeat window research (not enabled in printer)."""
import unittest

from tools.offline_window_lab import (
    SelectiveRepeatReceiver, SelectiveRepeatSender, simulate,
)


class WindowIntegrityTests(unittest.TestCase):
    def test_no_loss_window_can_fill_uart_more_efficiently(self):
        payload = bytes(range(256)) * 512
        baseline = simulate(payload, window=1, drop_data=0, drop_ack=0)
        fast = simulate(payload, window=4, drop_data=0, drop_ack=0)
        self.assertTrue(baseline["identical"])
        self.assertTrue(fast["identical"])
        self.assertGreater(
            fast["throughput_KiB_s"],
            baseline["throughput_KiB_s"] * 1.3
        )
        self.assertLessEqual(fast["throughput_KiB_s"], 150000 / 1024)

    def test_loss_and_missing_acks_preserve_original_bytes(self):
        payload = bytes(range(256)) * 400
        for seed in range(20):
            measured = simulate(
                payload, window=4, drop_data=0.02,
                drop_ack=0.01, seed=seed,
            )
            self.assertTrue(measured["identical"], (seed, measured))

    def test_receiver_delivers_only_in_order_without_duplication(self):
        receiver = SelectiveRepeatReceiver(window=4)
        self.assertEqual(receiver.receive(2, b"B"), 2)
        self.assertEqual(receiver.output, b"")
        self.assertEqual(receiver.receive(1, b"A"), 1)
        self.assertEqual(receiver.output, b"AB")
        self.assertEqual(receiver.receive(1, b"A"), 1)
        self.assertEqual(receiver.output, b"AB")
        self.assertIsNone(receiver.receive(9, b"INVALID"))
        self.assertEqual(receiver.output, b"AB")

    def test_sender_never_overruns_oldest_unacked_frame(self):
        sender = SelectiveRepeatSender(b"x" * (2044 * 6), window=4)
        emitted = sender.produce(0)
        self.assertEqual([seq for seq, _ in emitted], [1, 2, 3, 4])
        for seq in (2, 3, 4):
            self.assertTrue(sender.acknowledge(seq))
        self.assertEqual(sender.produce(0.02), [])
        self.assertTrue(sender.acknowledge(1))
        self.assertEqual([seq for seq, _ in sender.produce(0.03)], [5, 6])

    def test_timeout_resends_the_same_bytes(self):
        sender = SelectiveRepeatSender(b"original", window=1, timeout=0.1)
        first = sender.produce(0)
        self.assertEqual(first, [(1, b"original")])
        self.assertEqual(sender.produce(0.05), [])
        self.assertEqual(sender.produce(0.11), first)
        self.assertEqual(sender.retries, 1)
        self.assertTrue(sender.acknowledge(1))
        self.assertTrue(sender.done)

    def test_bad_window_bounds(self):
        for invalid in (0, 33):
            with self.assertRaises(ValueError):
                SelectiveRepeatSender(b"test", window=invalid)


if __name__ == "__main__":
    unittest.main()
