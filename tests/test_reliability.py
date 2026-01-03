# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.reliability import RttEstimator, RecvWindow, ReliabilityStats
from sfb.protocol import MAX_IN_FLIGHT, MIN_RTO_MS, MAX_RTO_MS


class RttEstimatorTests(unittest.TestCase):
    def test_first_sample_sets_rto(self):
        rtt = RttEstimator()
        rtt.add_sample(1000)
        self.assertEqual(rtt.rto_ms, 2000)

    def test_backoff_doubles(self):
        rtt = RttEstimator()
        rtt.add_sample(1000)
        rtt.backoff()
        self.assertEqual(rtt.rto_ms, 4000)

    def test_min_rto_clamp(self):
        rtt = RttEstimator()
        rtt.add_sample(1)
        self.assertEqual(rtt.rto_ms, MIN_RTO_MS)

    def test_max_rto_clamp(self):
        rtt = RttEstimator()
        rtt.add_sample(100000)
        self.assertEqual(rtt.rto_ms, MAX_RTO_MS)

    def test_reset_clears_srtt(self):
        rtt = RttEstimator(initial_rto_ms=1200)
        rtt.add_sample(1000)
        rtt.backoff()
        rtt.reset()
        self.assertEqual(rtt.rto_ms, 1200)
        self.assertIsNone(rtt.srtt_ms)


class RecvWindowTests(unittest.TestCase):
    def test_in_order_delivery(self):
        win = RecvWindow(max_buffer=4)
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [(0, b'a')])
        ready = win.receive(1, b'b')
        self.assertEqual(ready, [(1, b'b')])

    def test_out_of_order_buffering(self):
        win = RecvWindow(max_buffer=4)
        ready = win.receive(1, b'b')
        self.assertEqual(ready, [])
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [(0, b'a'), (1, b'b')])

    def test_duplicate_ignored(self):
        win = RecvWindow(max_buffer=4)
        win.receive(1, b'b')
        ready = win.receive(1, b'b')
        self.assertEqual(ready, [])
        self.assertEqual(len(win._buffer), 1)

    def test_duplicate_before_buffer_full(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=1, stats=stats)
        win.receive(1, b'b')
        win.receive(1, b'b')
        self.assertEqual(stats.recv_duplicates, 1)
        self.assertEqual(stats.recv_buffer_full, 0)

    def test_buffer_limit_drops_excess(self):
        win = RecvWindow(max_buffer=1)
        win.receive(2, b'c')
        win.receive(3, b'd')
        self.assertEqual(len(win._buffer), 1)
        self.assertEqual(win.ack, 0)

    def test_buffer_full_drops_out_of_order(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=1, stats=stats)
        win.receive(1, b'b')
        win.receive(2, b'c')
        self.assertEqual(stats.recv_buffer_full, 1)
        self.assertEqual(len(win._buffer), 1)
        self.assertIn(1, win._buffer)
        self.assertNotIn(2, win._buffer)

    def test_sack_ignores_beyond_window(self):
        win = RecvWindow(max_buffer=4)
        win.receive(257, b't')  # Beyond ack+256, can't represent in SACK
        self.assertEqual(win.sack, 0)

    def test_sack_bitmap(self):
        win = RecvWindow(max_buffer=4)
        win.receive(0, b'a')
        win.receive(2, b'c')
        self.assertEqual(win.ack, 1)
        self.assertEqual(win.sack, 1 << 0)

    def test_set_initial_seq_clears_buffer(self):
        win = RecvWindow(max_buffer=4)
        win.receive(2, b'c')
        win.set_initial_seq(5)
        self.assertEqual(win.ack, 5)
        self.assertEqual(len(win._buffer), 0)

    def test_set_max_buffer_trims(self):
        win = RecvWindow(max_buffer=4)
        win.receive(1, b'b')
        win.receive(2, b'c')
        win.receive(3, b'd')
        win.set_max_buffer(1)
        self.assertTrue(len(win._buffer) <= 1)

    def test_max_buffer_cap(self):
        win = RecvWindow()
        self.assertRaises(ValueError, win.set_max_buffer, MAX_IN_FLIGHT + 1)

    def test_rejects_beyond_sack_window(self):
        # Packets beyond ack+256 can't be represented in SACK, must be rejected
        win = RecvWindow(max_buffer=256)
        # next_expected = 0, so SACK window is 1-256
        # seq 257 is beyond the window
        ready = win.receive(257, b'too far')
        self.assertEqual(ready, [])
        self.assertEqual(len(win._buffer), 0)
        # seq 256 is within window (offset = 256, which is <= SACK_BITS)
        ready = win.receive(256, b'edge')
        self.assertEqual(ready, [])
        self.assertEqual(len(win._buffer), 1)


if __name__ == '__main__':
    unittest.main()
