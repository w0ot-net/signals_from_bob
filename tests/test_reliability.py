# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from tunnel.reliability import RttEstimator, SendWindow, RecvWindow
from tunnel.protocol import MAX_IN_FLIGHT, MIN_RTO_MS, MAX_RTO_MS


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


class SendWindowTests(unittest.TestCase):
    def test_send_assigns_seq(self):
        win = SendWindow(max_in_flight=4)
        seq1 = win.send(b'a', now=1.0)
        seq2 = win.send(b'b', now=2.0)
        self.assertEqual(seq1, 0)
        self.assertEqual(seq2, 1)
        self.assertEqual(win.unacked_count, 2)

    def test_cumulative_ack_uses_send_order(self):
        win = SendWindow(max_in_flight=4)
        win.send(b'a', now=1.0)
        win.send(b'b', now=2.0)
        win.send(b'c', now=3.0)
        samples = win.process_ack(ack=2, sack=0, now=5.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual(win.unacked_count, 1)
        oldest = win.get_oldest_unacked()
        self.assertEqual(oldest[1], b'c')

    def test_sack_ack(self):
        win = SendWindow(max_in_flight=4)
        win.send(b'a', now=1.0)  # seq 0
        win.send(b'b', now=2.0)  # seq 1
        win.send(b'c', now=3.0)  # seq 2
        sack = 1 << 0  # ack+1 (seq 1)
        samples = win.process_ack(ack=1, sack=sack, now=5.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual(win.unacked_count, 1)

    def test_oldest_unacked_skips_acked(self):
        win = SendWindow(max_in_flight=4)
        win.send(b'a', now=1.0)  # seq 0
        win.send(b'b', now=2.0)  # seq 1
        win.process_ack(ack=1, sack=0, now=3.0)
        oldest = win.get_oldest_unacked()
        self.assertEqual(oldest[0], 1)

    def test_mark_retransmit_does_not_block_window(self):
        win = SendWindow(max_in_flight=1)
        win.send(b'a', now=1.0)
        self.assertFalse(win.can_send)
        win.mark_retransmit(0, now=2.0)
        self.assertTrue(win.can_send)

    def test_get_retransmits(self):
        win = SendWindow(max_in_flight=2)
        win.send(b'a', now=1.0)
        retransmits = win.get_retransmits(rto_sec=0.5, now=1.4)
        self.assertEqual(retransmits, [])
        retransmits = win.get_retransmits(rto_sec=0.5, now=1.6)
        self.assertEqual(retransmits, [(0, b'a')])

    def test_ack_retransmit_has_no_rtt_sample(self):
        win = SendWindow(max_in_flight=1)
        seq = win.send(b'a', now=1.0)
        win.mark_retransmit(seq, now=2.0)
        samples = win.process_ack(ack=1, sack=0, now=3.0)
        self.assertEqual(samples, [])

    def test_max_in_flight_cap(self):
        self.assertRaises(ValueError, SendWindow, max_in_flight=MAX_IN_FLIGHT + 1)


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

    def test_buffer_limit_drops_excess(self):
        win = RecvWindow(max_buffer=1)
        win.receive(2, b'c')
        win.receive(3, b'd')
        self.assertEqual(len(win._buffer), 1)
        self.assertEqual(win.ack, 0)

    def test_sack_ignores_beyond_window(self):
        win = RecvWindow(max_buffer=4)
        win.receive(20, b't')
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
        # Packets beyond ack+16 can't be represented in SACK, must be rejected
        win = RecvWindow(max_buffer=16)
        # next_expected = 0, so SACK window is 1-16
        # seq 17 is beyond the window
        ready = win.receive(17, b'too far')
        self.assertEqual(ready, [])
        self.assertEqual(len(win._buffer), 0)
        # seq 16 is within window (offset = 16, which is <= SACK_BITS)
        ready = win.receive(16, b'edge')
        self.assertEqual(ready, [])
        self.assertEqual(len(win._buffer), 1)


if __name__ == '__main__':
    unittest.main()
