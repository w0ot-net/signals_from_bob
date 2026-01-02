# -*- coding: ascii -*-
"""
Adaptive pacing for Alice sends.
"""

from __future__ import absolute_import


class AdaptivePacer(object):
    """
    Adaptive pacing controller driven by inflight targets.
    """

    def __init__(self, enabled, target_inflight_ratio, min_inflight,
                 max_inflight, feedback_gain, ack_ewma_alpha, rtt_floor_ms,
                 ack_idle_reset_sec):
        self._enabled = bool(enabled)
        self._target_ratio = float(target_inflight_ratio)
        self._min_inflight = int(min_inflight)
        self._max_inflight = int(max_inflight) if max_inflight is not None else None
        self._feedback_gain = float(feedback_gain)
        self._ack_ewma_alpha = float(ack_ewma_alpha)
        self._rtt_floor_ms = float(rtt_floor_ms)
        self._ack_idle_reset_sec = float(ack_idle_reset_sec)
        self._ack_rate_ewma = None
        self._last_ack_time = None

    @property
    def enabled(self):
        return self._enabled

    def on_ack(self, acked_count, now):
        if not self._enabled:
            return
        if acked_count <= 0:
            return
        if self._last_ack_time is None:
            self._last_ack_time = now
            return
        dt = now - self._last_ack_time
        if dt <= 0:
            self._last_ack_time = now
            return
        if dt > self._ack_idle_reset_sec:
            self._ack_rate_ewma = None
            self._last_ack_time = None
            return
        rate = float(acked_count) / dt
        if self._ack_rate_ewma is None:
            self._ack_rate_ewma = rate
        else:
            alpha = self._ack_ewma_alpha
            self._ack_rate_ewma = (
                (1.0 - alpha) * self._ack_rate_ewma + alpha * rate
            )
        self._last_ack_time = now

    def _normalize_cap(self, cap):
        if cap < 1:
            return 1
        return cap

    def _clamp_target(self, target, cap):
        if target < self._min_inflight:
            target = self._min_inflight
        max_inflight = self._max_inflight if self._max_inflight is not None else cap
        if max_inflight < 1:
            max_inflight = 1
        if max_inflight > cap:
            max_inflight = cap
        if target > max_inflight:
            target = max_inflight
        if target > cap:
            target = cap
        return target

    def _base_target(self, cap):
        target = int(cap * self._target_ratio)
        return self._clamp_target(target, cap)

    def _feedback_target(self, cap, srtt_ms):
        if self._ack_rate_ewma is None or srtt_ms is None:
            return None
        rtt_ms = srtt_ms
        if rtt_ms < self._rtt_floor_ms:
            rtt_ms = self._rtt_floor_ms
        rtt_sec = rtt_ms / 1000.0
        pipe = self._ack_rate_ewma * rtt_sec
        target = int(pipe * self._feedback_gain)
        return self._clamp_target(target, cap)

    def target_inflight(self, cap, srtt_ms=None):
        cap = self._normalize_cap(cap)
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        if feedback_target is not None:
            return feedback_target
        return base_target

    def can_send(self, unacked_count, cap, srtt_ms=None):
        if not self._enabled:
            return True
        target = self.target_inflight(cap, srtt_ms=srtt_ms)
        return unacked_count < target

    def state_fields(self, unacked_count, cap, rate_limit=None, srtt_ms=None):
        cap = self._normalize_cap(cap)
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        target = base_target
        target_mode = 'base'
        if feedback_target is not None:
            target = feedback_target
            target_mode = 'feedback'
        fields = {
            'target_inflight': target,
            'base_target': base_target,
            'feedback_target': feedback_target,
            'target_mode': target_mode,
            'unacked_count': unacked_count,
            'cap': cap,
            'ack_rate_ewma': self._ack_rate_ewma,
            'srtt_ms': srtt_ms,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
