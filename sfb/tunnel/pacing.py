# -*- coding: ascii -*-
"""
Adaptive pacing for Alice sends.
"""

from __future__ import absolute_import


class AdaptivePacer(object):
    """
    Adaptive pacing controller driven by inflight targets.
    """

    _ACK_RATE_DROP_FACTOR = 0.8
    _PROBE_STEP = 1

    def __init__(self, enabled, target_inflight_ratio, min_inflight,
                 max_inflight, feedback_gain, ack_ewma_alpha,
                 retransmit_penalty, rtt_floor_ms, ack_idle_reset_sec):
        self._enabled = bool(enabled)
        self._target_ratio = float(target_inflight_ratio)
        self._min_inflight = int(min_inflight)
        self._max_inflight = int(max_inflight) if max_inflight is not None else None
        self._feedback_gain = float(feedback_gain)
        self._ack_ewma_alpha = float(ack_ewma_alpha)
        self._retransmit_ewma_alpha = float(ack_ewma_alpha)
        self._retransmit_penalty = float(retransmit_penalty)
        self._rtt_floor_ms = float(rtt_floor_ms)
        self._ack_idle_reset_sec = float(ack_idle_reset_sec)
        self._ack_rate_ewma = None
        self._last_ack_time = None
        self._retransmit_rate_ewma = None
        self._last_retransmit_time = None
        self._probe_extra = 0
        self._last_probe_time = None

    @property
    def enabled(self):
        return self._enabled

    def on_ack(self, acked_count, now, srtt_ms=None):
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
            self._reset_feedback()
            return
        rate = float(acked_count) / dt
        prev_rate = self._ack_rate_ewma
        if self._ack_rate_ewma is None:
            self._ack_rate_ewma = rate
        else:
            alpha = self._ack_ewma_alpha
            self._ack_rate_ewma = (
                (1.0 - alpha) * self._ack_rate_ewma + alpha * rate
            )
        self._last_ack_time = now
        if (prev_rate is not None and
                self._ack_rate_ewma < prev_rate * self._ACK_RATE_DROP_FACTOR):
            self._reset_probe(now)
            return
        if srtt_ms is None:
            return
        rtt_ms = srtt_ms
        if rtt_ms < self._rtt_floor_ms:
            rtt_ms = self._rtt_floor_ms
        rtt_sec = rtt_ms / 1000.0
        if rtt_sec <= 0:
            return
        if self._last_probe_time is None:
            self._last_probe_time = now
            return
        delta = now - self._last_probe_time
        if delta <= 0:
            return
        steps = int(delta / rtt_sec)
        if steps <= 0:
            return
        self._probe_extra += steps * self._PROBE_STEP
        self._last_probe_time += steps * rtt_sec

    def on_retransmit(self, now):
        if not self._enabled:
            return
        if self._last_retransmit_time is None:
            self._last_retransmit_time = now
            self._reset_probe(now)
            return
        dt = now - self._last_retransmit_time
        if dt <= 0:
            self._last_retransmit_time = now
            self._reset_probe(now)
            return
        if dt > self._ack_idle_reset_sec:
            self._reset_retransmit_feedback()
            self._reset_probe(now)
            return
        rate = 1.0 / dt
        if self._retransmit_rate_ewma is None:
            self._retransmit_rate_ewma = rate
        else:
            alpha = self._retransmit_ewma_alpha
            self._retransmit_rate_ewma = (
                (1.0 - alpha) * self._retransmit_rate_ewma + alpha * rate
            )
        self._last_retransmit_time = now
        self._reset_probe(now)

    def _reset_feedback(self):
        self._ack_rate_ewma = None
        self._last_ack_time = None
        self._reset_retransmit_feedback()
        self._reset_probe(None)

    def _reset_retransmit_feedback(self):
        self._retransmit_rate_ewma = None
        self._last_retransmit_time = None

    def _reset_probe(self, now):
        self._probe_extra = 0
        self._last_probe_time = now

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
        penalty = self._retransmit_penalty_factor()
        target = int((pipe * self._feedback_gain) / penalty)
        return self._clamp_target(target, cap)

    def _retransmit_ratio(self):
        if self._retransmit_rate_ewma is None:
            return None
        if self._ack_rate_ewma is None:
            return None
        ack_rate = self._ack_rate_ewma
        if ack_rate <= 0:
            return None
        ratio = self._retransmit_rate_ewma / ack_rate
        if ratio < 0:
            ratio = 0.0
        return ratio

    def _retransmit_penalty_factor(self):
        ratio = self._retransmit_ratio()
        if ratio is None:
            return 1.0
        penalty = 1.0 + ratio * self._retransmit_penalty
        if penalty <= 0:
            return 1.0
        return penalty

    def target_inflight(self, cap, srtt_ms=None):
        cap = self._normalize_cap(cap)
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        target = base_target
        if feedback_target is not None:
            target = feedback_target
        target += self._probe_extra
        return self._clamp_target(target, cap)

    def can_send(self, unacked_count, cap, srtt_ms=None):
        if not self._enabled:
            return True
        target = self.target_inflight(cap, srtt_ms=srtt_ms)
        return unacked_count < target

    def state_fields(self, unacked_count, cap, rate_limit=None, srtt_ms=None):
        cap = self._normalize_cap(cap)
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        baseline_target = base_target
        target_mode = 'base'
        if feedback_target is not None:
            baseline_target = feedback_target
            target_mode = 'feedback'
        probe_target = baseline_target + self._probe_extra
        target = self._clamp_target(probe_target, cap)
        if target > baseline_target:
            target_mode = 'probe'
        retransmit_ratio = self._retransmit_ratio()
        feedback_penalty = None
        if retransmit_ratio is not None:
            feedback_penalty = 1.0 + retransmit_ratio * self._retransmit_penalty
        fields = {
            'target_inflight': target,
            'base_target': base_target,
            'feedback_target': feedback_target,
            'baseline_target': baseline_target,
            'probe_extra': self._probe_extra,
            'probe_target': probe_target if self._probe_extra else None,
            'target_mode': target_mode,
            'unacked_count': unacked_count,
            'cap': cap,
            'ack_rate_ewma': self._ack_rate_ewma,
            'retransmit_rate_ewma': self._retransmit_rate_ewma,
            'retransmit_ratio': retransmit_ratio,
            'retransmit_penalty': self._retransmit_penalty,
            'feedback_penalty': feedback_penalty,
            'srtt_ms': srtt_ms,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
