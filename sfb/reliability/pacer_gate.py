# -*- coding: ascii -*-
"""
Pacer gating controller for Alice.
"""

from __future__ import absolute_import


class PacerGateController(object):
    """
    Encapsulate pacer gating and feedback-freeze decisions.
    """

    def check_send(self, send_window, pacer, now, srtt_ms, rto_sec,
                   min_age_ratio, keepalive_only, pacer_cap, max_window,
                   pacer_gate_cap=None, check_distance=True,
                   check_pacer=True):
        """
        Return a decision dict with gating and optional freeze details.
        """
        if check_distance:
            decision = self._check_window_distance(
                send_window,
                pacer,
                now,
                rto_sec,
                min_age_ratio,
                keepalive_only,
                pacer_cap,
                max_window,
            )
        else:
            decision = {
                'can_send': True,
                'block_reason': None,
                'block_details': None,
                'pacer_cap': pacer_cap,
            }
        if decision.get('can_send') and check_pacer:
            pacer_block = self._check_pacer(
                send_window,
                pacer,
                srtt_ms,
                keepalive_only,
                pacer_gate_cap,
            )
            if pacer_block is not None:
                decision['can_send'] = False
                decision['block_reason'] = 'pacer'
                decision['block_details'] = pacer_block
        return decision

    def _check_window_distance(self, send_window, pacer, now, rto_sec,
                               min_age_ratio, keepalive_only, pacer_cap,
                               max_window):
        exceeded, distance_info = send_window.distance_exceeded(
            max_window=max_window
        )
        decision = {
            'can_send': True,
            'block_reason': None,
            'block_details': None,
            'pacer_cap': pacer_cap,
        }
        if not exceeded:
            action, reason = self._maybe_unfreeze_feedback(
                pacer,
                now,
                reason='distance_clear',
            )
            if action is not None:
                decision['freeze_action'] = action
                decision['freeze_reason'] = reason
            return decision
        distance_details = send_window.distance_details(now=now)
        action, reason = self._update_feedback_freeze(
            pacer,
            now,
            distance_info,
            distance_details,
            keepalive_only,
            rto_sec,
            min_age_ratio,
            send_window,
        )
        if action is not None:
            decision['freeze_action'] = action
            decision['freeze_reason'] = reason
            decision['freeze_details'] = {
                'distance_info': distance_info,
                'details': distance_details,
            }
        decision['can_send'] = False
        decision['block_reason'] = 'window_distance'
        decision['block_details'] = {
            'keepalive_only': keepalive_only,
            'distance_info': distance_info,
            'distance_details': distance_details,
            'pacer_cap': pacer_cap,
        }
        return decision

    def _check_pacer(self, send_window, pacer, srtt_ms, keepalive_only, cap):
        if not self._pacer_enabled(pacer):
            return None
        if keepalive_only:
            return None
        if cap is None:
            cap = self._pacer_gate_cap(send_window)
        unacked, inflight = self._pacer_inflight_counts(send_window)
        if inflight is None:
            inflight = unacked
        if pacer.can_send(inflight, cap, srtt_ms=srtt_ms):
            return None
        return {
            'keepalive_only': keepalive_only,
            'unacked': unacked,
            'inflight': inflight,
            'cap': cap,
        }

    def _pacer_inflight_counts(self, send_window):
        unacked = send_window.unacked_count
        distance_info = send_window.distance_info()
        if distance_info is None:
            return unacked, None
        distance = distance_info[0]
        if distance < unacked:
            distance = unacked
        return unacked, distance

    def _pacer_gate_cap(self, send_window):
        cap = send_window._max_in_flight
        if cap < 1:
            cap = 1
        return cap

    def _maybe_unfreeze_feedback(self, pacer, now, reason):
        if not self._pacer_enabled(pacer):
            return (None, None)
        if pacer.unfreeze_feedback(now):
            return ('unfreeze', reason)
        return (None, None)

    def _update_feedback_freeze(self, pacer, now, distance_info, details,
                                keepalive_only, rto_sec, min_age_ratio,
                                send_window):
        if not self._pacer_enabled(pacer):
            return (None, None)
        if keepalive_only:
            return (None, None)
        should_freeze = self._should_freeze_feedback(
            pacer,
            distance_info,
            details,
            rto_sec,
            min_age_ratio,
            send_window,
        )
        if should_freeze:
            if pacer.freeze_feedback(now, reason='sack_stall'):
                return ('freeze', 'sack_stall')
            return (None, None)
        if pacer.unfreeze_feedback(now):
            return ('unfreeze', 'stall_clear')
        return (None, None)

    def _should_freeze_feedback(self, pacer, distance_info, details,
                                rto_sec, min_age_ratio, send_window):
        if not self._pacer_enabled(pacer):
            return False
        if details is None:
            return False
        if not details.get('missing_in_unacked'):
            return False
        missing_age = details.get('missing_age')
        if missing_age is None:
            return False
        min_age = rto_sec * min_age_ratio
        if missing_age < min_age:
            return False
        (distance, max_in_flight, effective_cap, unacked,
         _distance_limit, _last_cum_ack, _next_seq) = distance_info
        buffered = distance - unacked
        cap = effective_cap
        if cap is None:
            cap = max_in_flight
        if cap is None:
            cap = send_window._max_in_flight
        if cap < 1:
            cap = 1
        low_unacked = max(2, int(cap * 0.25))
        high_buffered = max(4, int(cap * 0.5))
        if unacked > low_unacked:
            return False
        if buffered < high_buffered:
            return False
        return True

    def _pacer_enabled(self, pacer):
        return pacer is not None and pacer.enabled
