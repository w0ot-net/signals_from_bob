# -*- coding: ascii -*-
"""Tests for adaptive pacing."""

from __future__ import absolute_import

import unittest

from sfb.tunnel.pacing import AdaptivePacer


class AdaptivePacerTests(unittest.TestCase):
    def test_target_clamp(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.1, min_inflight=3,
            max_inflight=None, rtt_floor_ms=5.0, fast_start=True
        )
        self.assertEqual(pacer.target_inflight(10), 3)

        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.9, min_inflight=1,
            max_inflight=4, rtt_floor_ms=5.0, fast_start=True
        )
        self.assertEqual(pacer.target_inflight(10), 4)

        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.9, min_inflight=1,
            max_inflight=None, rtt_floor_ms=5.0, fast_start=True
        )
        self.assertEqual(pacer.target_inflight(3), 2)

    def test_pacing_interval(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=1.0, min_inflight=1,
            max_inflight=None, rtt_floor_ms=100.0, fast_start=False,
            time_based=True
        )
        cap = 4
        now = 0.0
        self.assertTrue(pacer.can_send(now, 0, cap, srtt_ms=100.0))
        pacer.on_send(now, 0, cap, srtt_ms=100.0)
        self.assertFalse(pacer.can_send(0.01, 0, cap, srtt_ms=100.0))
        self.assertTrue(pacer.can_send(0.03, 0, cap, srtt_ms=100.0))

    def test_fast_start_burst(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=1.0, min_inflight=1,
            max_inflight=None, rtt_floor_ms=100.0, fast_start=True,
            time_based=True
        )
        cap = 4
        pacer.on_send(0.0, 0, cap, srtt_ms=100.0)
        pacer.on_response(True)
        self.assertTrue(pacer.can_send(0.01, 0, cap, srtt_ms=100.0))

        pacer.on_send(0.01, 0, cap, srtt_ms=100.0)
        self.assertTrue(pacer.fast_start_active)
        pacer.on_send(0.02, 1, cap, srtt_ms=100.0)
        pacer.on_send(0.03, 2, cap, srtt_ms=100.0)
        pacer.on_send(0.04, 3, cap, srtt_ms=100.0)
        self.assertFalse(pacer.fast_start_active)
        self.assertFalse(pacer.can_send(0.05, 3, cap, srtt_ms=100.0))

    def test_no_time_based_gate(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=1.0, min_inflight=1,
            max_inflight=None, rtt_floor_ms=100.0, fast_start=False,
            time_based=False
        )
        cap = 4
        pacer.on_send(0.0, 0, cap, srtt_ms=100.0)
        self.assertTrue(pacer.can_send(0.01, 0, cap, srtt_ms=100.0))


if __name__ == '__main__':
    unittest.main()
