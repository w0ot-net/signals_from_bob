# -*- coding: ascii -*-
"""Tests for adaptive pacing."""

from __future__ import absolute_import

import unittest

from sfb.tunnel.pacing import AdaptivePacer


def make_pacer(**kwargs):
    defaults = {
        'enabled': True,
        'target_inflight_ratio': 1.0,
        'min_inflight': 1,
        'max_inflight': None,
        'feedback_gain': 1.0,
        'ack_ewma_alpha': 0.2,
        'rtt_floor_ms': 5.0,
        'ack_idle_reset_sec': 2.0,
    }
    defaults.update(kwargs)
    return AdaptivePacer(**defaults)


class AdaptivePacerTests(unittest.TestCase):
    def test_target_clamp(self):
        pacer = make_pacer(target_inflight_ratio=0.1, min_inflight=3)
        self.assertEqual(pacer.target_inflight(10), 3)

        pacer = make_pacer(target_inflight_ratio=0.9, min_inflight=1, max_inflight=4)
        self.assertEqual(pacer.target_inflight(10), 4)

        pacer = make_pacer(target_inflight_ratio=0.9, min_inflight=1)
        self.assertEqual(pacer.target_inflight(3), 2)

    def test_can_send_blocks_at_target(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1)
        cap = 4
        self.assertTrue(pacer.can_send(1, cap))
        self.assertFalse(pacer.can_send(2, cap))

    def test_disabled_allows(self):
        pacer = make_pacer(enabled=False, target_inflight_ratio=0.5, min_inflight=1)
        self.assertTrue(pacer.can_send(10, 1))

    def test_ack_ewma_updates(self):
        pacer = make_pacer(ack_ewma_alpha=0.5)
        pacer.on_ack(1, now=1.0)
        self.assertIsNone(pacer._ack_rate_ewma)
        pacer.on_ack(1, now=2.0)
        self.assertAlmostEqual(pacer._ack_rate_ewma, 1.0)
        pacer.on_ack(3, now=3.0)
        self.assertAlmostEqual(pacer._ack_rate_ewma, 2.0)

    def test_ack_idle_reset(self):
        pacer = make_pacer(ack_idle_reset_sec=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0)
        self.assertIsNotNone(pacer._ack_rate_ewma)
        pacer.on_ack(1, now=4.5)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertIsNone(pacer._last_ack_time)

    def test_target_uses_feedback(self):
        pacer = make_pacer(target_inflight_ratio=0.1)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(5, now=2.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 5)

    def test_target_falls_back_without_srtt(self):
        pacer = make_pacer(target_inflight_ratio=0.2)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(4, now=2.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=None), 2)

    def test_on_ack_ignored_when_zero(self):
        pacer = make_pacer()
        pacer.on_ack(0, now=1.0)
        self.assertIsNone(pacer._last_ack_time)


if __name__ == '__main__':
    unittest.main()
