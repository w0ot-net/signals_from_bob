# -*- coding: ascii -*-
"""Tests for adaptive pacing."""

from __future__ import absolute_import

import unittest

from sfb.tunnel.pacing import AdaptivePacer


class AdaptivePacerTests(unittest.TestCase):
    def test_target_clamp(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.1, min_inflight=3,
            max_inflight=None
        )
        self.assertEqual(pacer.target_inflight(10), 3)

        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.9, min_inflight=1,
            max_inflight=4
        )
        self.assertEqual(pacer.target_inflight(10), 4)

        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.9, min_inflight=1,
            max_inflight=None
        )
        self.assertEqual(pacer.target_inflight(3), 2)

    def test_can_send_blocks_at_target(self):
        pacer = AdaptivePacer(
            True, target_inflight_ratio=0.5, min_inflight=1,
            max_inflight=None
        )
        cap = 4
        self.assertTrue(pacer.can_send(1, cap))
        self.assertFalse(pacer.can_send(2, cap))

    def test_disabled_allows(self):
        pacer = AdaptivePacer(
            False, target_inflight_ratio=0.5, min_inflight=1,
            max_inflight=None
        )
        self.assertTrue(pacer.can_send(10, 1))


if __name__ == '__main__':
    unittest.main()
