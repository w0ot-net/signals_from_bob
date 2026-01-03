# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.reliability import RttEstimator, SendWindow, RecvWindow, ReliabilityStats
from sfb.protocol import (
    FLAG_KEEPALIVE,
    MAX_IN_FLIGHT,
    MIN_RTO_MS,
    MAX_RTO_MS,
    SACK_BITS,
    SEQ_MAX,
)


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

    def test_add_sample_updates_srtt_and_rto(self):
        rtt = RttEstimator()
        rtt.add_sample(1000)
        rtt.add_sample(500)
        self.assertAlmostEqual(rtt.srtt_ms, 937.5)
        self.assertAlmostEqual(rtt.rto_ms, 1875.0)

    def test_rto_sec_reports_seconds(self):
        rtt = RttEstimator(initial_rto_ms=1500)
        self.assertAlmostEqual(rtt.rto_sec, 1.5)

    def test_reset_backoff_uses_srtt(self):
        rtt = RttEstimator()
        rtt.add_sample(1000)
        rtt.backoff()
        rtt.reset_backoff()
        self.assertEqual(rtt.rto_ms, 2000)

    def test_custom_min_clamp(self):
        rtt = RttEstimator(initial_rto_ms=800, min_rto_ms=700, max_rto_ms=900)
        rtt.add_sample(10)
        self.assertEqual(rtt.rto_ms, 700)

    def test_custom_max_clamp(self):
        rtt = RttEstimator(initial_rto_ms=800, min_rto_ms=100, max_rto_ms=150)
        rtt.add_sample(1000)
        self.assertEqual(rtt.rto_ms, 150)

    def test_reset_backoff_without_srtt(self):
        rtt = RttEstimator(initial_rto_ms=1200)
        rtt.backoff()
        self.assertEqual(rtt.rto_ms, 2400)
        rtt.reset_backoff()
        self.assertEqual(rtt.rto_ms, 2400)

    def test_backoff_clamps_to_max(self):
        rtt = RttEstimator(initial_rto_ms=MAX_RTO_MS)
        rtt.backoff()
        self.assertEqual(rtt.rto_ms, MAX_RTO_MS)



class SendWindowTests(unittest.TestCase):
    def test_send_raises_when_full(self):
        win = SendWindow(max_in_flight=1)
        win.send([b'a'], now=1.0)
        self.assertRaises(ValueError, win.send, [b'b'], now=2.0)

    def test_drop_keepalive_only(self):
        win = SendWindow(max_in_flight=3)
        keepalive_seq = win.send([], flags=FLAG_KEEPALIVE, now=1.0)
        data_seq = win.send([b'a'], now=2.0)
        self.assertTrue(win.drop_keepalive(keepalive_seq))
        self.assertFalse(win.drop_keepalive(data_seq))
        self.assertNotIn(keepalive_seq, win._unacked)
        self.assertIn(data_seq, win._unacked)

    def test_drop_oldest_keepalive(self):
        win = SendWindow(max_in_flight=3)
        seq0 = win.send([], flags=FLAG_KEEPALIVE, now=1.0)
        seq1 = win.send([], flags=FLAG_KEEPALIVE, now=2.0)
        self.assertEqual(win.drop_oldest_keepalive(), seq0)
        self.assertNotIn(seq0, win._unacked)
        self.assertIn(seq1, win._unacked)
        self.assertEqual(win.drop_oldest_keepalive(), seq1)
        self.assertEqual(win.unacked_count, 0)

        win = SendWindow(max_in_flight=1)
        win.send([b'a'], now=1.0)
        self.assertIsNone(win.drop_oldest_keepalive())

    def test_get_unacked_info(self):
        win = SendWindow(max_in_flight=2)
        seq = win.send([b'a'], encrypted_body=b'x', now=1.0)
        info = win.get_unacked_info(seq)
        self.assertEqual(
            info,
            (seq, [b'a'], 0, b'x', 1.0, 0),
        )
        self.assertIsNone(win.get_unacked_info(12345))

    def test_get_unacked_in_sack_window_orders_and_filters(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)  # seq 0
        win.send([b'b'], now=2.0)  # seq 1
        win.send([b'c'], now=3.0)  # seq 2
        win.send([b'd'], now=4.0)  # seq 3
        seqs = win.get_unacked_in_sack_window(ack=2)
        self.assertEqual(seqs, [2, 3])
        seqs = win.get_unacked_in_sack_window(ack=2, max_offset=0)
        self.assertEqual(seqs, [2])

    def test_get_oldest_unacked_empty(self):
        win = SendWindow(max_in_flight=1)
        self.assertIsNone(win.get_oldest_unacked())

    def test_old_ack_does_not_advance(self):
        win = SendWindow(max_in_flight=2)
        win.send([b'a'], now=1.0)  # seq 0
        win.process_ack(ack=1, sack=0, now=2.0)
        win.send([b'b'], now=3.0)  # seq 1
        samples, acked, data_acked = win.process_ack(ack=1, sack=0, now=4.0)
        self.assertEqual(samples, [])
        self.assertEqual(acked, 0)
        self.assertEqual(data_acked, 0)
        self.assertEqual(win.unacked_count, 1)

    def test_sack_missing_seq_ignored(self):
        win = SendWindow(max_in_flight=4)
        win.send([b'a'], now=1.0)  # seq 0
        win.send([b'b'], now=2.0)  # seq 1
        sack = 1 << 2  # ack+3, not sent
        samples, acked, data_acked = win.process_ack(ack=0, sack=sack, now=3.0)
        self.assertEqual(samples, [])
        self.assertEqual(acked, 0)
        self.assertEqual(data_acked, 0)
        self.assertEqual(win.unacked_count, 2)

    def test_sack_wraps_sequence_space(self):
        win = SendWindow(max_in_flight=2)
        win._next_seq = SEQ_MAX
        seq_max = win.send([b'a'], now=1.0)
        seq_zero = win.send([b'b'], now=2.0)
        samples, acked, data_acked = win.process_ack(
            ack=SEQ_MAX, sack=1, now=3.0
        )
        self.assertEqual(seq_max, SEQ_MAX)
        self.assertEqual(seq_zero, 0)
        self.assertEqual(samples, [1000.0])
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 1)
        self.assertIn(seq_max, win._unacked)
        self.assertNotIn(seq_zero, win._unacked)

    def test_send_window_stats(self):
        stats = ReliabilityStats()
        win = SendWindow(max_in_flight=4, stats=stats)
        seq0 = win.send([b'a'], now=1.0)
        win.send([b'b'], now=2.0)
        self.assertEqual(stats.sent_packets, 2)
        win.mark_retransmit(seq0, now=2.5)
        self.assertEqual(stats.retransmit_packets, 1)
        samples, acked, data_acked = win.process_ack(
            ack=0, sack=1 << 0, now=3.0
        )
        self.assertEqual(samples, [1000.0])
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 1)
        self.assertEqual(stats.acked_packets, 1)
        self.assertEqual(stats.acked_sack_packets, 1)
        self.assertEqual(stats.acked_first_tx_packets, 1)
        self.assertEqual(stats.rtt_samples, 1)
        samples, acked, data_acked = win.process_ack(ack=1, sack=0, now=4.0)
        self.assertEqual(samples, [])
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 1)
        self.assertEqual(stats.acked_packets, 2)
        self.assertEqual(stats.acked_cumulative_packets, 1)
        self.assertEqual(stats.acked_sack_packets, 1)
        self.assertEqual(stats.acked_first_tx_packets, 1)
        self.assertEqual(stats.rtt_samples, 1)
        self.assertEqual(win.unacked_count, 0)


class ReliabilityStatsTests(unittest.TestCase):
    def test_retransmit_skip_counters(self):
        stats = ReliabilityStats()
        stats.on_retransmit_skip_rate_limit()
        stats.on_retransmit_skip_rate_limit()
        stats.on_retransmit_skip_transport()
        self.assertEqual(stats.retransmit_skipped_rate_limit, 2)
        self.assertEqual(stats.retransmit_skipped_transport, 1)


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

    def test_duplicate_below_ack_counts(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=4, stats=stats)
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [(0, b'a')])
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [])
        self.assertEqual(stats.recv_duplicates, 1)

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

    def test_out_of_window_increments_stats(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=4, stats=stats)
        win.receive(257, b't')
        self.assertEqual(stats.recv_out_of_window, 1)
        self.assertEqual(stats.recv_buffered, 0)
        self.assertEqual(stats.recv_delivered, 0)

    def test_buffered_and_delivered_stats_out_of_order(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=4, stats=stats)
        ready = win.receive(1, b'b')
        self.assertEqual(ready, [])
        self.assertEqual(stats.recv_buffered, 1)
        self.assertEqual(stats.recv_delivered, 0)
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [(0, b'a'), (1, b'b')])
        self.assertEqual(stats.recv_delivered, 2)

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

    def test_sack_bitmap_multiple_entries(self):
        win = RecvWindow(max_buffer=4)
        win.receive(2, b'c')
        win.receive(4, b'e')
        self.assertEqual(win.sack, (1 << 1) | (1 << 3))

    def test_sack_bitmap_wraparound(self):
        win = RecvWindow(max_buffer=4)
        win.set_initial_seq(SEQ_MAX)
        win.receive(0, b'a')
        win.receive(1, b'b')
        self.assertEqual(win.ack, SEQ_MAX)
        self.assertEqual(win.sack, (1 << 0) | (1 << 1))

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

    def test_set_max_buffer_trims_farthest(self):
        win = RecvWindow(max_buffer=4)
        win.receive(1, b'b')
        win.receive(2, b'c')
        win.receive(3, b'd')
        win.receive(4, b'e')
        win.set_max_buffer(2)
        self.assertEqual(sorted(win._buffer.keys()), [1, 2])

    def test_set_max_buffer_trims_wraparound_farthest(self):
        win = RecvWindow(max_buffer=4)
        win.set_initial_seq((SEQ_MAX - 1) & SEQ_MAX)
        win.receive(SEQ_MAX, b'a')
        win.receive(0, b'b')
        win.receive(1, b'c')
        win.set_max_buffer(2)
        self.assertEqual(sorted(win._buffer.keys()), [0, SEQ_MAX])

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

    def test_out_of_window_stats(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=4, stats=stats)
        win.receive(SACK_BITS + 1, b'oob')
        self.assertEqual(stats.recv_out_of_window, 1)

    def test_delivered_stats_in_order(self):
        stats = ReliabilityStats()
        win = RecvWindow(max_buffer=4, stats=stats)
        ready = win.receive(0, b'a')
        self.assertEqual(ready, [(0, b'a')])
        self.assertEqual(stats.recv_buffered, 0)
        self.assertEqual(stats.recv_delivered, 1)

    def test_wraparound_delivery(self):
        win = RecvWindow(max_buffer=4)
        win.set_initial_seq(SEQ_MAX)
        ready = win.receive(0, b'wrap')
        self.assertEqual(ready, [])
        ready = win.receive(SEQ_MAX, b'last')
        self.assertEqual(ready, [(SEQ_MAX, b'last'), (0, b'wrap')])
        self.assertEqual(win.ack, 1)


if __name__ == '__main__':
    unittest.main()
