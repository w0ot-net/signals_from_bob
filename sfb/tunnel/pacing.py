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
    _FEEDBACK_MIN_SAMPLES = 3
    _BLOCK_SMALL_UNACKED = 2
    _BLOCK_FAST_REDUCTION = 0.25
    _BLOCK_SLOW_REDUCTION = 0.125
    _BLOCK_COOLDOWN_RTT_FACTOR = 1.0

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
        self._ack_samples = 0
        self._feedback_min_samples = self._FEEDBACK_MIN_SAMPLES
        self._probe_extra = 0
        self._last_probe_time = None
        self._block_penalty = 0
        self._block_reason = None
        self._last_block_time = None

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
        self._ack_samples += 1
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
        advance_steps = steps
        if self._block_penalty > 0:
            decay = min(self._block_penalty, steps * self._PROBE_STEP)
            self._block_penalty -= decay
            if self._block_penalty <= 0:
                self._block_penalty = 0
                self._block_reason = None
            decay_steps = int(decay / self._PROBE_STEP)
            steps -= decay_steps
        if steps > 0:
            self._probe_extra += steps * self._PROBE_STEP
        self._last_probe_time += advance_steps * rtt_sec

    def on_retransmit(self, now):
        if not self._enabled:
            return
        self._reset_probe(now)

    def on_blocked(self, reason, now, cap, srtt_ms=None, unacked_count=None):
        if not self._enabled:
            return False
        cap = self._normalize_cap(cap)
        cooldown = self._block_cooldown(srtt_ms)
        if (self._last_block_time is not None and
                now - self._last_block_time < cooldown):
            return False
        _, _, baseline_target, _ = self._baseline_target(cap, srtt_ms)
        current_target = self.target_inflight(cap, srtt_ms=srtt_ms)
        reduction = self._blocked_reduction(
            reason, current_target, unacked_count
        )
        if reduction <= 0:
            return False
        max_penalty = baseline_target - self._min_inflight
        if max_penalty < 0:
            max_penalty = 0
        new_penalty = self._block_penalty + reduction
        if new_penalty > max_penalty:
            new_penalty = max_penalty
        if new_penalty <= self._block_penalty:
            return False
        self._block_penalty = new_penalty
        self._block_reason = reason
        self._last_block_time = now
        self._reset_probe(now)
        return True

    def _reset_feedback(self):
        self._ack_rate_ewma = None
        self._last_ack_time = None
        self._ack_samples = 0
        self._reset_probe(None)

    def _reset_probe(self, now):
        self._probe_extra = 0
        self._last_probe_time = now

    def _feedback_reduction_ready(self):
        return self._ack_samples >= self._feedback_min_samples

    def _block_cooldown(self, srtt_ms):
        rtt_ms = srtt_ms if srtt_ms is not None else self._rtt_floor_ms
        if rtt_ms < self._rtt_floor_ms:
            rtt_ms = self._rtt_floor_ms
        rtt_sec = rtt_ms / 1000.0
        if rtt_sec <= 0:
            return 0.0
        return rtt_sec * self._BLOCK_COOLDOWN_RTT_FACTOR

    def _blocked_reduction(self, reason, current_target, unacked_count):
        if current_target <= self._min_inflight:
            return 0
        if reason == 'window_distance':
            if (unacked_count is not None and
                    unacked_count <= self._BLOCK_SMALL_UNACKED):
                factor = self._BLOCK_FAST_REDUCTION
            else:
                factor = self._BLOCK_SLOW_REDUCTION
        elif reason in ('transport_headroom', 'window_full'):
            factor = self._BLOCK_SLOW_REDUCTION
        else:
            return 0
        reduction = int(current_target * factor)
        if reduction < 1:
            reduction = 1
        return reduction

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

    def _baseline_target(self, cap, srtt_ms):
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        baseline_target = base_target
        target_mode = 'base'
        if feedback_target is not None:
            if feedback_target > base_target:
                baseline_target = feedback_target
                target_mode = 'feedback'
            elif (feedback_target < base_target and
                  self._feedback_reduction_ready()):
                baseline_target = feedback_target
                target_mode = 'feedback'
        return base_target, feedback_target, baseline_target, target_mode

    def target_inflight(self, cap, srtt_ms=None):
        cap = self._normalize_cap(cap)
        _, _, baseline_target, _ = self._baseline_target(cap, srtt_ms)
        blocked_target = baseline_target - self._block_penalty
        target = blocked_target + self._probe_extra
        return self._clamp_target(target, cap)

    def can_send(self, unacked_count, cap, srtt_ms=None):
        if not self._enabled:
            return True
        target = self.target_inflight(cap, srtt_ms=srtt_ms)
        return unacked_count < target

    def state_fields(self, unacked_count, cap, rate_limit=None, srtt_ms=None):
        cap = self._normalize_cap(cap)
        base_target, feedback_target, baseline_target, target_mode = (
            self._baseline_target(cap, srtt_ms)
        )
        blocked_target = baseline_target - self._block_penalty
        probe_target = blocked_target + self._probe_extra
        target = self._clamp_target(probe_target, cap)
        if target > blocked_target:
            target_mode = 'probe'
        block_target = None
        if self._block_penalty:
            block_target = self._clamp_target(blocked_target, cap)
        fields = {
            'target_inflight': target,
            'base_target': base_target,
            'feedback_target': feedback_target,
            'baseline_target': baseline_target,
            'block_penalty': self._block_penalty,
            'block_reason': self._block_reason,
            'block_target': block_target,
            'probe_extra': self._probe_extra,
            'probe_target': probe_target if self._probe_extra else None,
            'target_mode': target_mode,
            'unacked_count': unacked_count,
            'cap': cap,
            'ack_rate_ewma': self._ack_rate_ewma,
            'ack_samples': self._ack_samples,
            'srtt_ms': srtt_ms,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
