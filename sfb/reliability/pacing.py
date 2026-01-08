# -*- coding: ascii -*-
"""
Adaptive pacing for Alice sends.
"""

from __future__ import absolute_import

from collections import namedtuple


_PacerState = namedtuple('_PacerState', [
    'cap',
    'base_target',
    'feedback_target',
    'baseline_target',
    'blocked_target',
    'probe_target',
    'target_inflight',
    'target_mode',
    'feedback_floor',
    'feedback_floor_active',
    'block_target',
])

class AdaptivePacer(object):
    """
    Adaptive pacing controller driven by inflight targets.
    """

    # Drop threshold for EWMA ACK rate. Lower values make probes reset on
    # smaller slowdowns; higher values tolerate more variation before reset.
    _ACK_RATE_DROP_FACTOR = 0.8
    # Probe increment per RTT step. Increase to ramp inflight faster; decrease
    # to make probing more conservative.
    _PROBE_STEP = 1
    # Minimum ACK samples before allowing feedback to reduce inflight. Higher
    # values delay reductions to avoid oscillation on sparse ACKs.
    _FEEDBACK_MIN_SAMPLES = 3
    # Treat large ACK gaps as idle relative to RTT to avoid feedback collapse.
    _ACK_IDLE_RTT_FACTOR = 6.0
    _ACK_IDLE_MIN_SEC = 0.25
    # "Small unacked" threshold for aggressive window-distance cuts. Lower
    # values make the fast reduction trigger less often.
    _BLOCK_SMALL_UNACKED = 2
    # Reduction fraction when window_distance stalls with small unacked.
    # Increase to cut faster; decrease to preserve throughput.
    _BLOCK_FAST_REDUCTION = 0.25
    # Reduction fraction for milder stalls (window_full or window_distance
    # with higher unacked).
    _BLOCK_SLOW_REDUCTION = 0.125
    # Cooldown window between stall reductions, measured in RTT multiples.
    # Increase to reduce repeated cuts; decrease to respond faster.
    _BLOCK_COOLDOWN_RTT_FACTOR = 1.0
    # Floor fraction of cap when feedback would reduce inflight without loss.
    _FEEDBACK_FLOOR_CAP_RATIO = 0.5
    # Require this many RTTs of no loss before applying the feedback floor.
    _FEEDBACK_FLOOR_RTT_FACTOR = 4.0
    # Apply only a fraction of feedback reductions to dampen cuts.
    _FEEDBACK_REDUCTION_GAIN = 0.10

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
        self._feedback_frozen = False
        self._feedback_frozen_reason = None
        self._feedback_frozen_since = None
        self._last_retransmit_time = None
        self._last_window_distance_time = None
        self._last_sack_time = None

    @property
    def enabled(self):
        return self._enabled

    @property
    def feedback_frozen(self):
        return self._feedback_frozen

    def freeze_feedback(self, now, reason=None):
        if not self._enabled:
            return False
        if self._feedback_frozen:
            return False
        self._feedback_frozen = True
        self._feedback_frozen_reason = reason
        self._feedback_frozen_since = now
        return True

    def unfreeze_feedback(self, now):
        if not self._enabled:
            return False
        if not self._feedback_frozen:
            return False
        self._feedback_frozen = False
        self._feedback_frozen_reason = None
        self._feedback_frozen_since = None
        if self._last_ack_time is None:
            self._last_ack_time = now
        return True

    def on_ack(self, acked_count, now, srtt_ms=None, sack=None):
        if not self._enabled:
            return
        if acked_count <= 0:
            return
        if sack:
            self._last_sack_time = now
        if self._feedback_frozen:
            self._last_ack_time = now
            return
        if self._last_ack_time is None:
            self._last_ack_time = now
            return
        dt = now - self._last_ack_time
        if dt <= 0:
            self._last_ack_time = now
            return
        idle_reset = self._ack_idle_reset_sec
        if srtt_ms is not None:
            idle_reset = self._ack_idle_threshold(srtt_ms)
        if dt > idle_reset:
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
        self._last_retransmit_time = now
        self._reset_probe(now)

    def on_blocked(self, reason, now, cap, srtt_ms=None, unacked_count=None):
        if not self._enabled:
            return False
        if reason == 'window_distance':
            self._last_window_distance_time = now
        cooldown = self._block_cooldown(srtt_ms)
        if (self._last_block_time is not None and
                now - self._last_block_time < cooldown):
            return False
        state = self._target_state(cap, srtt_ms=srtt_ms, now=now)
        baseline_target = state.baseline_target
        current_target = state.target_inflight
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

    def _ack_idle_threshold(self, srtt_ms):
        rtt_ms = srtt_ms
        if rtt_ms < self._rtt_floor_ms:
            rtt_ms = self._rtt_floor_ms
        rtt_sec = rtt_ms / 1000.0
        idle_reset = rtt_sec * self._ACK_IDLE_RTT_FACTOR
        if idle_reset < self._ACK_IDLE_MIN_SEC:
            idle_reset = self._ACK_IDLE_MIN_SEC
        if idle_reset > self._ack_idle_reset_sec:
            idle_reset = self._ack_idle_reset_sec
        return idle_reset

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
        elif reason == 'window_full':
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

    def _resolve_now(self, now):
        if self._last_ack_time is None:
            return None
        if now is None:
            return self._last_ack_time
        if now < self._last_ack_time:
            return now
        return self._last_ack_time

    def _last_loss_time(self):
        last = None
        for ts in (self._last_retransmit_time,
                   self._last_window_distance_time,
                   self._last_sack_time):
            if ts is None:
                continue
            if last is None or ts > last:
                last = ts
        return last

    def _no_loss_recent(self, now, srtt_ms):
        if now is None or srtt_ms is None:
            return False
        last_loss = self._last_loss_time()
        if last_loss is None:
            return True
        rtt_ms = srtt_ms
        if rtt_ms < self._rtt_floor_ms:
            rtt_ms = self._rtt_floor_ms
        rtt_sec = rtt_ms / 1000.0
        if rtt_sec <= 0:
            return False
        return (now - last_loss) >= (rtt_sec * self._FEEDBACK_FLOOR_RTT_FACTOR)

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

    def base_target_inflight(self, cap):
        cap = self._normalize_cap(cap)
        return self._base_target(cap)

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

    def _apply_feedback_reduction(self, base_target, feedback_target):
        if feedback_target >= base_target:
            return feedback_target
        gain = self._FEEDBACK_REDUCTION_GAIN
        if gain >= 1.0:
            return feedback_target
        if gain <= 0.0:
            return base_target
        reduction = base_target - feedback_target
        scaled = int(reduction * gain)
        if scaled < 1:
            scaled = 1
        target = base_target - scaled
        if target < feedback_target:
            target = feedback_target
        return target

    def _feedback_floor(self, cap, base_target, srtt_ms, now):
        ratio = self._FEEDBACK_FLOOR_CAP_RATIO
        if ratio <= 0:
            return None
        now = self._resolve_now(now)
        if not self._no_loss_recent(now, srtt_ms):
            return None
        floor = int(cap * ratio)
        if floor < self._min_inflight:
            floor = self._min_inflight
        if floor > base_target:
            floor = base_target
        return floor

    def _baseline_target(self, cap, srtt_ms, now=None):
        base_target = self._base_target(cap)
        feedback_target = self._feedback_target(cap, srtt_ms)
        baseline_target = base_target
        target_mode = 'base'
        feedback_floor = None
        feedback_floor_active = False
        if feedback_target is not None:
            if feedback_target > base_target:
                baseline_target = feedback_target
                target_mode = 'feedback'
            elif (feedback_target < base_target and
                  self._feedback_reduction_ready()):
                baseline_target = self._apply_feedback_reduction(
                    base_target,
                    feedback_target,
                )
                target_mode = 'feedback'
        feedback_floor = self._feedback_floor(
            cap,
            base_target,
            srtt_ms,
            now,
        )
        if feedback_floor is not None and baseline_target < feedback_floor:
            baseline_target = feedback_floor
            feedback_floor_active = True
            if target_mode == 'feedback':
                target_mode = 'feedback_floor'
            else:
                target_mode = 'floor'
        return (base_target, feedback_target, baseline_target,
                target_mode, feedback_floor, feedback_floor_active)

    def _target_state(self, cap, srtt_ms=None, now=None):
        cap = self._normalize_cap(cap)
        (base_target, feedback_target, baseline_target, target_mode,
         feedback_floor, feedback_floor_active) = self._baseline_target(
            cap, srtt_ms, now
        )
        blocked_target = baseline_target - self._block_penalty
        blocked_target = self._apply_block_floor(blocked_target, feedback_target)
        probe_target = blocked_target + self._probe_extra
        target = self._clamp_target(probe_target, cap)
        if target > blocked_target:
            target_mode = 'probe'
        block_target = None
        if self._block_penalty:
            block_target = self._clamp_target(blocked_target, cap)
        return _PacerState(
            cap=cap,
            base_target=base_target,
            feedback_target=feedback_target,
            baseline_target=baseline_target,
            blocked_target=blocked_target,
            probe_target=probe_target,
            target_inflight=target,
            target_mode=target_mode,
            feedback_floor=feedback_floor,
            feedback_floor_active=feedback_floor_active,
            block_target=block_target,
        )

    def _apply_block_floor(self, blocked_target, feedback_target):
        if self._block_reason != 'window_distance':
            return blocked_target
        if feedback_target is None:
            return blocked_target
        if blocked_target < feedback_target:
            return feedback_target
        return blocked_target

    def target_state(self, cap, srtt_ms=None, now=None):
        return self._target_state(cap, srtt_ms=srtt_ms, now=now)

    def target_inflight(self, cap, srtt_ms=None, now=None, state=None):
        if state is None:
            state = self._target_state(cap, srtt_ms=srtt_ms, now=now)
        return state.target_inflight

    def can_send(self, unacked_count, cap, srtt_ms=None, now=None, state=None):
        if not self._enabled:
            return True
        if state is None:
            state = self._target_state(cap, srtt_ms=srtt_ms, now=now)
        return unacked_count < state.target_inflight

    def state_fields(self, unacked_count, cap, rate_limit=None, srtt_ms=None,
                     now=None, state=None):
        if state is None:
            state = self._target_state(cap, srtt_ms=srtt_ms, now=now)
        target = state.target_inflight
        fields = {
            'target_inflight': target,
            'base_target': state.base_target,
            'feedback_target': state.feedback_target,
            'baseline_target': state.baseline_target,
            'feedback_floor': state.feedback_floor,
            'feedback_floor_active': state.feedback_floor_active,
            'feedback_reduction_gain': self._FEEDBACK_REDUCTION_GAIN,
            'block_penalty': self._block_penalty,
            'block_reason': self._block_reason,
            'block_target': state.block_target,
            'probe_extra': self._probe_extra,
            'probe_target': (
                state.probe_target if self._probe_extra else None
            ),
            'target_mode': state.target_mode,
            'unacked_count': unacked_count,
            'cap': state.cap,
            'ack_rate_ewma': self._ack_rate_ewma,
            'ack_samples': self._ack_samples,
            'srtt_ms': srtt_ms,
            'feedback_frozen': self._feedback_frozen,
            'feedback_frozen_reason': self._feedback_frozen_reason,
            'feedback_frozen_since': self._feedback_frozen_since,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
