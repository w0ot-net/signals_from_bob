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

    def test_ack_ewma_alpha_zero_holds_value(self):
        pacer = make_pacer(ack_ewma_alpha=0.0, ack_idle_reset_sec=100.0)
        pacer.on_ack(10, now=1.0)
        pacer.on_ack(10, now=2.0)
        self.assertEqual(pacer._ack_rate_ewma, 10.0)
        pacer.on_ack(1, now=3.0)
        self.assertEqual(pacer._ack_rate_ewma, 10.0)

    def test_ack_ewma_alpha_one_tracks_latest_rate(self):
        pacer = make_pacer(ack_ewma_alpha=1.0, ack_idle_reset_sec=100.0)
        pacer.on_ack(4, now=1.0)
        pacer.on_ack(4, now=2.0)
        self.assertEqual(pacer._ack_rate_ewma, 4.0)
        pacer.on_ack(2, now=4.0)
        self.assertEqual(pacer._ack_rate_ewma, 1.0)

    def test_ack_idle_reset(self):
        pacer = make_pacer(ack_idle_reset_sec=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0)
        self.assertIsNotNone(pacer._ack_rate_ewma)
        pacer.on_ack(1, now=4.5)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertIsNone(pacer._last_ack_time)

    def test_ack_idle_reset_boundary_does_not_reset(self):
        pacer = make_pacer(ack_idle_reset_sec=1.0, ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0)
        self.assertEqual(pacer._ack_rate_ewma, 1.0)
        self.assertEqual(pacer._last_ack_time, 2.0)

    def test_ack_idle_reset_clears_probe_state(self):
        pacer = make_pacer(ack_idle_reset_sec=100.0, ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0, srtt_ms=1000.0)
        pacer.on_ack(1, now=5.0, srtt_ms=1000.0)
        self.assertGreater(pacer._probe_extra, 0)
        self.assertIsNotNone(pacer._last_probe_time)
        pacer.on_ack(1, now=250.0)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertIsNone(pacer._last_ack_time)
        self.assertEqual(pacer._probe_extra, 0)
        self.assertIsNone(pacer._last_probe_time)

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

    def test_on_ack_ignored_when_negative(self):
        pacer = make_pacer()
        pacer._last_ack_time = 1.0
        pacer.on_ack(-1, now=2.0)
        self.assertEqual(pacer._last_ack_time, 1.0)
        self.assertIsNone(pacer._ack_rate_ewma)

    def test_probe_increases_per_rtt(self):
        pacer = make_pacer(target_inflight_ratio=0.1, ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0, srtt_ms=1000.0)
        pacer.on_ack(1, now=2.0, srtt_ms=1000.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 1)
        pacer.on_ack(1, now=3.1, srtt_ms=1000.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 2)

    def test_probe_resets_on_rate_drop(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
        )
        pacer.on_ack(1, now=1.0, srtt_ms=1000.0)
        pacer.on_ack(4, now=2.0, srtt_ms=1000.0)
        pacer.on_ack(4, now=3.1, srtt_ms=1000.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 4)
        pacer.on_ack(1, now=4.1, srtt_ms=1000.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 1)

    def test_rate_drop_equal_threshold_does_not_reset_probe(self):
        pacer = make_pacer(ack_ewma_alpha=1.0, ack_idle_reset_sec=100.0)
        pacer.on_ack(10, now=1.0)
        pacer.on_ack(10, now=2.0)
        pacer._probe_extra = 2
        pacer._last_probe_time = 1.0
        pacer.on_ack(8, now=3.0)
        self.assertEqual(pacer._ack_rate_ewma, 8.0)
        self.assertEqual(pacer._probe_extra, 2)
        self.assertEqual(pacer._last_probe_time, 1.0)

    def test_probe_resets_on_retransmit(self):
        pacer = make_pacer(target_inflight_ratio=0.1, ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0, srtt_ms=1000.0)
        pacer.on_ack(1, now=2.0, srtt_ms=1000.0)
        pacer.on_ack(1, now=3.1, srtt_ms=1000.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 2)
        pacer.on_retransmit(now=3.2)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 1)

    def test_on_ack_non_positive_dt_ignored(self):
        pacer = make_pacer(ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=1.0)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertEqual(pacer._last_ack_time, 1.0)
        pacer.on_ack(1, now=0.5)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertEqual(pacer._last_ack_time, 0.5)

    def test_rtt_floor_limits_probe_step(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
            rtt_floor_ms=1000.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0, srtt_ms=1.0)
        self.assertEqual(pacer._probe_extra, 0)
        pacer.on_ack(1, now=3.1, srtt_ms=1.0)
        self.assertEqual(pacer._probe_extra, 1)

    def test_target_uses_rtt_floor_for_feedback(self):
        pacer = make_pacer(
            target_inflight_ratio=0.0,
            min_inflight=1,
            ack_ewma_alpha=1.0,
            rtt_floor_ms=1000.0,
        )
        pacer.on_ack(2, now=1.0)
        pacer.on_ack(2, now=2.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1.0), 2)

    def test_feedback_lower_than_base_uses_base(self):
        pacer = make_pacer(target_inflight_ratio=0.9, ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=3.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 9)

    def test_on_ack_disabled_does_not_update_state(self):
        pacer = make_pacer(enabled=False)
        pacer.on_ack(1, now=1.0, srtt_ms=1000.0)
        self.assertIsNone(pacer._last_ack_time)
        self.assertIsNone(pacer._ack_rate_ewma)
        self.assertIsNone(pacer._last_probe_time)
        self.assertEqual(pacer._probe_extra, 0)

    def test_on_retransmit_disabled_does_not_reset_probe(self):
        pacer = make_pacer(enabled=False)
        pacer._probe_extra = 2
        pacer._last_probe_time = 3.0
        pacer.on_retransmit(now=4.0)
        self.assertEqual(pacer._probe_extra, 2)
        self.assertEqual(pacer._last_probe_time, 3.0)

    def test_probe_delta_non_positive_no_change(self):
        pacer = make_pacer(ack_ewma_alpha=1.0, ack_idle_reset_sec=100.0)
        pacer._last_ack_time = 2.0
        pacer._last_probe_time = 3.0
        pacer.on_ack(1, now=2.5, srtt_ms=1000.0)
        self.assertEqual(pacer._probe_extra, 0)
        self.assertEqual(pacer._last_probe_time, 3.0)

    def test_probe_steps_zero_no_change(self):
        pacer = make_pacer(ack_ewma_alpha=1.0, ack_idle_reset_sec=100.0)
        pacer._last_ack_time = 2.0
        pacer._last_probe_time = 2.0
        pacer.on_ack(1, now=2.5, srtt_ms=1000.0)
        self.assertEqual(pacer._probe_extra, 0)
        self.assertEqual(pacer._last_probe_time, 2.0)

    def test_probe_multiple_steps_accumulate(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
        )
        pacer.on_ack(1, now=1.0, srtt_ms=1000.0)
        pacer.on_ack(2, now=2.0, srtt_ms=1000.0)
        pacer.on_ack(4, now=4.2, srtt_ms=1000.0)
        self.assertEqual(pacer._probe_extra, 2)
        self.assertAlmostEqual(pacer._last_probe_time, 4.0)

    def test_probe_skips_when_rtt_non_positive(self):
        pacer = make_pacer(
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
            rtt_floor_ms=0.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0, srtt_ms=0.0)
        self.assertIsNone(pacer._last_probe_time)
        self.assertEqual(pacer._probe_extra, 0)

    def test_probe_skips_when_rtt_floor_negative(self):
        pacer = make_pacer(
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
            rtt_floor_ms=-1.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=2.0, srtt_ms=-5.0)
        self.assertIsNone(pacer._last_probe_time)
        self.assertEqual(pacer._probe_extra, 0)

    def test_cap_normalization_and_max_floor(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1, max_inflight=0)
        self.assertEqual(pacer.target_inflight(0), 1)

    def test_negative_cap_normalizes_to_one(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1)
        self.assertEqual(pacer.target_inflight(-5), 1)

    def test_min_inflight_over_cap_clamps(self):
        pacer = make_pacer(target_inflight_ratio=0.1, min_inflight=10)
        self.assertEqual(pacer.target_inflight(4), 4)

    def test_max_inflight_below_min_inflight_clamps(self):
        pacer = make_pacer(target_inflight_ratio=0.1, min_inflight=5, max_inflight=3)
        self.assertEqual(pacer.target_inflight(10), 3)

    def test_max_inflight_over_cap_clamps(self):
        pacer = make_pacer(target_inflight_ratio=2.0, min_inflight=1, max_inflight=10)
        self.assertEqual(pacer.target_inflight(4), 4)

    def test_probe_extra_clamped_to_cap(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1)
        pacer._probe_extra = 10
        self.assertEqual(pacer.target_inflight(4), 4)

    def test_feedback_gain_truncates(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            feedback_gain=2.0,
            ack_idle_reset_sec=100.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(39, now=11.0)
        self.assertEqual(pacer.target_inflight(10, srtt_ms=1000.0), 7)

    def test_can_send_uses_feedback_target(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(5, now=2.0, srtt_ms=1000.0)
        self.assertTrue(pacer.can_send(4, 10, srtt_ms=1000.0))
        self.assertFalse(pacer.can_send(5, 10, srtt_ms=1000.0))

    def test_state_fields_base_and_rate_limit(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1)
        fields = pacer.state_fields(unacked_count=2, cap=4, rate_limit=123)
        self.assertEqual(fields['target_inflight'], 2)
        self.assertEqual(fields['base_target'], 2)
        self.assertIsNone(fields['feedback_target'])
        self.assertEqual(fields['baseline_target'], 2)
        self.assertEqual(fields['probe_extra'], 0)
        self.assertIsNone(fields['probe_target'])
        self.assertEqual(fields['target_mode'], 'base')
        self.assertEqual(fields['rate_limit'], 123)

    def test_state_fields_exposes_ack_rate_and_srtt(self):
        pacer = make_pacer(ack_ewma_alpha=1.0)
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(2, now=2.0)
        fields = pacer.state_fields(unacked_count=1, cap=4, srtt_ms=1000.0)
        self.assertEqual(fields['ack_rate_ewma'], 2.0)
        self.assertEqual(fields['srtt_ms'], 1000.0)
        self.assertNotIn('rate_limit', fields)

    def test_state_fields_feedback_and_probe(self):
        pacer = make_pacer(
            target_inflight_ratio=0.1,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
            rtt_floor_ms=1000.0,
        )
        pacer.on_ack(5, now=1.0)
        pacer.on_ack(5, now=2.0, srtt_ms=1000.0)
        fields = pacer.state_fields(unacked_count=1, cap=10, srtt_ms=1000.0)
        self.assertEqual(fields['target_inflight'], 5)
        self.assertEqual(fields['target_mode'], 'feedback')
        self.assertIsNone(fields['probe_target'])
        pacer.on_ack(5, now=3.0, srtt_ms=1000.0)
        fields = pacer.state_fields(unacked_count=1, cap=10, srtt_ms=1000.0)
        self.assertEqual(fields['target_inflight'], 6)
        self.assertEqual(fields['probe_target'], 6)
        self.assertEqual(fields['target_mode'], 'probe')

    def test_state_fields_probe_clamped_to_cap(self):
        pacer = make_pacer(target_inflight_ratio=0.5, min_inflight=1)
        pacer._probe_extra = 10
        fields = pacer.state_fields(unacked_count=1, cap=4)
        self.assertEqual(fields['probe_target'], 12)
        self.assertEqual(fields['target_inflight'], 4)
        self.assertEqual(fields['target_mode'], 'probe')

    def test_state_fields_feedback_below_base_uses_base(self):
        pacer = make_pacer(
            target_inflight_ratio=0.9,
            ack_ewma_alpha=1.0,
            ack_idle_reset_sec=100.0,
        )
        pacer.on_ack(1, now=1.0)
        pacer.on_ack(1, now=3.0)
        fields = pacer.state_fields(unacked_count=1, cap=10, srtt_ms=1000.0)
        self.assertEqual(fields['base_target'], 9)
        self.assertEqual(fields['feedback_target'], 1)
        self.assertEqual(fields['target_mode'], 'base')


if __name__ == '__main__':
    unittest.main()
