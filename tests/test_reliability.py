# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.reliability import RttEstimator, SendWindow, RecvWindow, ReliabilityStats
from sfb.protocol import MAX_IN_FLIGHT, MIN_RTO_MS, MAX_RTO_MS, SEQ_MAX, FLAG_KEEPALIVE


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


class SendWindowTests(unittest.TestCase):
    def test_send_assigns_seq(self):
        win = SendWindow(max_in_flight=4)
        seq1 = win.send([b'a'], now=1.0)
        seq2 = win.send([b'b'], now=2.0)
        self.assertEqual(seq1, 0)
        self.assertEqual(seq2, 1)
        self.assertEqual(win.unacked_count, 2)

    def test_cumulative_ack_uses_send_order(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)
        win.send([b'b'], now=2.0)
        win.send([b'c'], now=3.0)
        samples, acked, data_acked = win.process_ack(ack=2, sack=0, now=5.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual(acked, 2)
        self.assertEqual(data_acked, 2)
        self.assertEqual(win.unacked_count, 1)
        oldest = win.get_oldest_unacked()
        self.assertEqual(oldest[1], [b'c'])

    def test_sack_ack(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)  # seq 0
        win.send([b'b'], now=2.0)  # seq 1
        win.send([b'c'], now=3.0)  # seq 2
        sack = 1 << 0  # ack+1 (seq 1)
        samples, acked, data_acked = win.process_ack(ack=1, sack=sack, now=5.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual(acked, 2)
        self.assertEqual(data_acked, 2)
        self.assertEqual(win.unacked_count, 1)

    def test_sack_only_progress_removes_acked(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)  # seq 0
        win.send([b'b'], now=2.0)  # seq 1 (missing)
        win.send([b'c'], now=3.0)  # seq 2
        win.send([b'd'], now=4.0)  # seq 3
        sack = (1 << 0) | (1 << 1)  # ack+1, ack+2
        win.process_ack(ack=1, sack=sack, now=5.0)
        self.assertEqual(win.unacked_count, 1)
        self.assertIn(1, win._unacked)
        self.assertNotIn(2, win._unacked)
        self.assertNotIn(3, win._unacked)

    def test_oldest_unacked_skips_acked(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)  # seq 0
        win.send([b'b'], now=2.0)  # seq 1
        win.process_ack(ack=1, sack=0, now=3.0)
        oldest = win.get_oldest_unacked()
        self.assertEqual(oldest[0], 1)

    def test_mark_retransmit_does_not_block_window(self):
        win = SendWindow(max_in_flight=1)
        win.send([b'a'], now=1.0)
        self.assertFalse(win.can_send)
        win.mark_retransmit(0, now=2.0)
        self.assertFalse(win.can_send)

    def test_get_retransmits(self):
        win = SendWindow(max_in_flight=2)
        win.send([b'a'], now=1.0)
        retransmits = win.get_retransmits(rto_sec=0.5, now=1.4)
        self.assertEqual(retransmits, [])
        retransmits = win.get_retransmits(rto_sec=0.5, now=1.6)
        self.assertEqual(retransmits, [(0, [b'a'], 0, None)])

    def test_get_retransmits_orders_by_send_time(self):
        win = SendWindow(max_in_flight=2)
        seq0 = win.send([b'a'], now=1.0)
        seq1 = win.send([b'b'], now=2.0)
        win.mark_retransmit(seq0, now=5.0)
        retransmits = win.get_retransmits(rto_sec=1.0, now=6.0, max_count=1)
        self.assertEqual(retransmits, [(seq1, [b'b'], 0, None)])

    def test_ack_retransmit_has_no_rtt_sample(self):
        win = SendWindow(max_in_flight=1)
        seq = win.send([b'a'], now=1.0)
        win.mark_retransmit(seq, now=2.0)
        samples, acked, data_acked = win.process_ack(ack=1, sack=0, now=3.0)
        self.assertEqual(samples, [])
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 1)

    def test_keepalive_ack_skips_rtt_sample(self):
        win = SendWindow(max_in_flight=1)
        win.send([], flags=FLAG_KEEPALIVE, now=1.0)
        samples, acked, data_acked = win.process_ack(ack=1, sack=0, now=2.0)
        self.assertEqual(samples, [])
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 0)

    def test_oldest_unacked_uses_send_time(self):
        win = SendWindow(max_in_flight=4)
        seq0 = win.send([b'a'], now=1.0)
        seq1 = win.send([b'b'], now=2.0)
        win.send([b'c'], now=3.0)
        win.mark_retransmit(seq0, now=4.0)
        oldest = win.get_oldest_unacked_info()
        self.assertEqual(oldest[0], seq1)

    def test_get_retransmits_does_not_update_send_time(self):
        win = SendWindow(max_in_flight=2)
        seq = win.send([b'a'], now=1.0)
        send_time = win._unacked[seq].send_time
        win.get_retransmits(rto_sec=0.0, now=2.0)
        self.assertEqual(win._unacked[seq].send_time, send_time)

    def test_cumulative_ack_wraps_sequence_space(self):
        win = SendWindow(max_in_flight=4)
        win._next_seq = (SEQ_MAX - 1) & SEQ_MAX
        seq_a = win.send([b'a'], now=1.0)
        seq_b = win.send([b'b'], now=2.0)
        seq_c = win.send([b'c'], now=3.0)
        win.process_ack(ack=0, sack=0, now=4.0)
        self.assertEqual(win.unacked_count, 1)
        self.assertIn(seq_c, win._unacked)
        self.assertNotIn(seq_a, win._unacked)
        self.assertNotIn(seq_b, win._unacked)

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
