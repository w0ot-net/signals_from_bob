# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.reliability import SendWindow, ReliabilityStats
from sfb.protocol import MAX_IN_FLIGHT, SEQ_MAX, FLAG_KEEPALIVE, SACK_BITS


class SendWindowTests(unittest.TestCase):
    def test_send_assigns_seq(self):
        win = SendWindow(max_in_flight=4)
        seq1 = win.send([b'a'], now=1.0)
        seq2 = win.send([b'b'], now=2.0)
        self.assertEqual(seq1, 0)
        self.assertEqual(seq2, 1)
        self.assertEqual(win.unacked_count, 2)

    def test_send_window_full_raises(self):
        win = SendWindow(max_in_flight=1)
        win.send([b'a'], now=1.0)
        self.assertRaises(ValueError, win.send, [b'b'], 0, None, 2.0)

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
        sack = 1 << 0  # ack+1 (seq 2)
        samples, acked, data_acked = win.process_ack(ack=1, sack=sack, now=5.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual(acked, 2)
        self.assertEqual(data_acked, 2)
        self.assertEqual(win.unacked_count, 1)

    def test_sack_progress_ready_on_ack_advance(self):
        win = SendWindow(max_in_flight=4)
        win.process_ack_with_progress(ack=1, sack=1, now=1.0)
        self.assertTrue(win.sack_progress_ready())
        win.process_ack_with_progress(ack=2, sack=0, now=2.0)
        self.assertFalse(win.sack_progress_ready())

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

    def test_sack_ack_wraps_sequence_space(self):
        win = SendWindow(max_in_flight=2)
        win._next_seq = SEQ_MAX
        seq_max = win.send([b'a'], now=1.0)
        seq_zero = win.send([b'b'], now=2.0)
        sack = 1 << 0  # ack+1 -> seq 0
        samples, acked, data_acked = win.process_ack(
            ack=SEQ_MAX, sack=sack, now=3.0
        )
        self.assertEqual(acked, 1)
        self.assertEqual(data_acked, 1)
        self.assertEqual(len(samples), 1)
        self.assertIn(seq_max, win._unacked)
        self.assertNotIn(seq_zero, win._unacked)

    def test_stats_send_and_ack_counts(self):
        stats = ReliabilityStats()
        win = SendWindow(max_in_flight=4, stats=stats)
        win.send([b'a'], now=1.0)
        win.send([b'b'], now=2.0)
        win.send([b'c'], now=3.0)
        self.assertEqual(stats.sent_packets, 3)
        sack = 1 << 0  # ack+1
        win.process_ack(ack=1, sack=sack, now=5.0)
        self.assertEqual(stats.acked_packets, 2)
        self.assertEqual(stats.acked_cumulative_packets, 1)
        self.assertEqual(stats.acked_sack_packets, 1)
        self.assertEqual(stats.acked_first_tx_packets, 2)
        self.assertEqual(stats.rtt_samples, 2)

    def test_stats_retransmit_skips_first_tx(self):
        stats = ReliabilityStats()
        win = SendWindow(max_in_flight=1, stats=stats)
        seq = win.send([b'a'], now=1.0)
        win.mark_retransmit(seq, now=2.0)
        self.assertEqual(stats.retransmit_packets, 1)
        win.process_ack(ack=1, sack=0, now=3.0)
        self.assertEqual(stats.acked_packets, 1)
        self.assertEqual(stats.acked_cumulative_packets, 1)
        self.assertEqual(stats.acked_first_tx_packets, 0)
        self.assertEqual(stats.rtt_samples, 0)

    def test_get_retransmits_max_count_zero(self):
        win = SendWindow(max_in_flight=1)
        win.send([b'a'], now=1.0)
        retransmits = win.get_retransmits(rto_sec=0.0, now=2.0, max_count=0)
        self.assertEqual(retransmits, [])
        retransmits = win.get_retransmits(rto_sec=0.0, now=2.0, max_count=-1)
        self.assertEqual(retransmits, [])

    def test_empty_window_helpers(self):
        win = SendWindow(max_in_flight=1)
        self.assertIsNone(win.get_oldest_unacked())
        self.assertIsNone(win.get_oldest_unacked_info())
        self.assertIsNone(win.get_unacked_info(0))
        self.assertFalse(win.drop_keepalive(0))
        self.assertIsNone(win.drop_oldest_keepalive())
        self.assertEqual(win.get_retransmits(rto_sec=0.0, now=1.0), [])
        win.mark_retransmit(0, now=1.0)
        self.assertEqual(win._retransmit_count, 0)

    def test_mark_retransmit_missing_seq_no_stats(self):
        stats = ReliabilityStats()
        win = SendWindow(max_in_flight=1, stats=stats)
        win.mark_retransmit(0, now=1.0)
        self.assertEqual(stats.retransmit_packets, 0)

    def test_get_unacked_in_sack_window_orders_by_offset(self):
        win = SendWindow(max_in_flight=4)
        win._next_seq = 9
        seq_behind = win.send([b'a'], now=1.0)
        seq_ack = win.send([b'b'], now=2.0)
        seq_ahead = win.send([b'c'], now=3.0)
        win._next_seq = 15
        seq_out = win.send([b'd'], now=4.0)
        unacked = win.get_unacked_in_sack_window(ack=seq_ack, max_offset=1)
        self.assertEqual(unacked, [seq_ack, seq_ahead])
        self.assertNotIn(seq_behind, unacked)
        self.assertNotIn(seq_out, unacked)

    def test_get_unacked_in_sack_window_default_max_offset(self):
        win = SendWindow(max_in_flight=4)
        ack = 10
        win._next_seq = (ack + SACK_BITS - 1) & SEQ_MAX
        seq_before = win.send([b'a'], now=1.0)
        seq_edge = win.send([b'b'], now=2.0)
        seq_out = win.send([b'c'], now=3.0)
        unacked = win.get_unacked_in_sack_window(ack=ack)
        self.assertEqual(unacked, [seq_before, seq_edge])
        self.assertNotIn(seq_out, unacked)

    def test_get_unacked_in_sack_window_wraps_sequence_space(self):
        win = SendWindow(max_in_flight=3)
        win._next_seq = SEQ_MAX
        seq_max = win.send([b'a'], now=1.0)
        seq_zero = win.send([b'b'], now=2.0)
        seq_one = win.send([b'c'], now=3.0)
        unacked = win.get_unacked_in_sack_window(ack=SEQ_MAX, max_offset=2)
        self.assertEqual(unacked, [seq_max, seq_zero, seq_one])

    def test_drop_keepalive_only_removes_keepalive(self):
        win = SendWindow(max_in_flight=2)
        keepalive_seq = win.send([], flags=FLAG_KEEPALIVE, now=1.0)
        data_seq = win.send([b'a'], now=2.0)
        self.assertFalse(win.drop_keepalive(data_seq))
        self.assertTrue(win.drop_keepalive(keepalive_seq))
        self.assertEqual(win.unacked_count, 1)
        self.assertIn(data_seq, win._unacked)

    def test_drop_oldest_keepalive(self):
        win = SendWindow(max_in_flight=3)
        data_seq = win.send([b'a'], now=1.0)
        keepalive_seq1 = win.send([], flags=FLAG_KEEPALIVE, now=2.0)
        keepalive_seq2 = win.send([], flags=FLAG_KEEPALIVE, now=3.0)
        dropped_seq = win.drop_oldest_keepalive()
        self.assertEqual(dropped_seq, keepalive_seq1)
        self.assertIn(data_seq, win._unacked)
        self.assertNotIn(keepalive_seq1, win._unacked)
        self.assertIn(keepalive_seq2, win._unacked)
        dropped_seq = win.drop_oldest_keepalive()
        self.assertEqual(dropped_seq, keepalive_seq2)
        self.assertNotIn(keepalive_seq2, win._unacked)
        self.assertIsNone(win.drop_oldest_keepalive())

    def test_get_unacked_info_returns_fields(self):
        win = SendWindow(max_in_flight=1)
        seq = win.send([b'a'], flags=0, encrypted_body=b'cipher', now=1.0)
        info = win.get_unacked_info(seq)
        self.assertEqual(
            info,
            (seq, [b'a'], 0, b'cipher', 1.0, 0),
        )

    def test_max_in_flight_cap(self):
        self.assertRaises(ValueError, SendWindow, max_in_flight=MAX_IN_FLIGHT + 1)


if __name__ == '__main__':
    unittest.main()
