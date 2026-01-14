# -*- coding: ascii -*-
"""
Pacer logging helpers.
"""

from __future__ import absolute_import


class PacerLoggingHelper(object):
    """
    Helper for assembling pacer log fields and summary bookkeeping.
    """

    def __init__(self, summary_interval):
        self._summary_interval = summary_interval
        self._last_target = None
        self._summary_last_time = None
        self._summary_last_sent = 0
        self._summary_last_recv = 0
        self._summary_last_stats = None
        self._target_sum = 0.0
        self._target_count = 0
        self._blocked_counts = {
            'window_distance': 0,
            'window_full': 0,
        }
        self._summary_last_blocked = None

    @property
    def summary_interval(self):
        return self._summary_interval

    def summary_action(self, now):
        interval = self._summary_interval
        if interval <= 0:
            return 'disabled'
        if self._summary_last_time is None:
            return 'init'
        elapsed = now - self._summary_last_time
        if elapsed < interval:
            return 'skip'
        if elapsed <= 0:
            return 'reset'
        return 'log'

    def note_blocked(self, reason):
        if reason in self._blocked_counts:
            self._blocked_counts[reason] += 1

    def maybe_target_event(self, pacer, unacked_count, cap, srtt_ms, side,
                           reason=None):
        if not pacer.enabled:
            return None
        fields = pacer.state_fields(
            unacked_count,
            cap,
            srtt_ms=srtt_ms,
        )
        target = fields.get('target_inflight')
        if target is None:
            return None
        if self._last_target == target:
            return None
        prev_target = self._last_target
        self._last_target = target
        feedback_adjust = self._should_feedback_adjust(prev_target, fields)
        event_fields = dict(fields)
        event_fields['previous_target_inflight'] = prev_target
        event_fields['side'] = side
        if reason is not None:
            event_fields['reason'] = reason
        return {
            'fields': event_fields,
            'target': target,
            'prev_target': prev_target,
            'feedback_adjust': feedback_adjust,
        }

    def adjust_fields(self, pacer, unacked_count, cap, srtt_ms, side,
                      prev_target, reason, block_reason=None):
        if prev_target is None:
            return None
        fields = pacer.state_fields(
            unacked_count,
            cap,
            srtt_ms=srtt_ms,
        )
        event_fields = dict(fields)
        event_fields['previous_target_inflight'] = prev_target
        event_fields['side'] = side
        event_fields['reason'] = reason
        if block_reason is not None:
            event_fields['block_reason'] = block_reason
        return event_fields

    def state_fields(self, pacer, unacked_count, cap, srtt_ms, side,
                     action=None, inflight_count=None):
        if not pacer.enabled:
            return None
        fields = pacer.state_fields(
            unacked_count,
            cap,
            srtt_ms=srtt_ms,
        )
        if inflight_count is not None and inflight_count != unacked_count:
            fields['inflight_count'] = inflight_count
        if self._summary_interval > 0:
            target = fields.get('target_inflight')
            if target is not None:
                self._target_sum += target
                self._target_count += 1
        fields['side'] = side
        if action is not None:
            fields['action'] = action
        return fields

    def maybe_summary_fields(self, now, pacer, send_window, max_window,
                             state, packets_sent, packets_received, transport,
                             cap, srtt_ms, stats_enabled, stats_snapshot):
        interval = self._summary_interval
        if interval <= 0:
            return None
        if self._summary_last_time is None:
            self._summary_last_time = now
            self._summary_last_sent = packets_sent
            self._summary_last_recv = packets_received
            self._target_sum = 0.0
            self._target_count = 0
            self._summary_last_blocked = dict(self._blocked_counts)
            if stats_enabled:
                self._summary_last_stats = stats_snapshot
            return None
        elapsed = now - self._summary_last_time
        if elapsed < interval:
            return None
        if elapsed <= 0:
            self._summary_last_time = now
            self._target_sum = 0.0
            self._target_count = 0
            return None
        sent_delta = packets_sent - self._summary_last_sent
        recv_delta = packets_received - self._summary_last_recv
        send_rate = float(sent_delta) / elapsed
        recv_rate = float(recv_delta) / elapsed
        pending = None
        max_in_flight = None
        if hasattr(transport, 'pending_count'):
            try:
                pending = transport.pending_count()
            except Exception:
                pending = None
        if hasattr(transport, 'max_in_flight'):
            try:
                max_in_flight = transport.max_in_flight
            except Exception:
                max_in_flight = None

        pacer_fields = pacer.state_fields(
            send_window.unacked_count,
            cap,
            srtt_ms=srtt_ms,
        )
        fields = {
            'side': 'alice',
            'state': state,
            'interval': round(elapsed, 6),
            'sent_delta': sent_delta,
            'recv_delta': recv_delta,
            'send_rate': round(send_rate, 6),
            'recv_rate': round(recv_rate, 6),
            'unacked': send_window.unacked_count,
            'send_window_max': send_window._max_in_flight,
            'pacer_enabled': pacer.enabled,
        }
        if pending is not None:
            fields['pending'] = pending
        if max_in_flight is not None:
            fields['transport_max_in_flight'] = max_in_flight
        ack_silence = send_window.ack_silence(now=now)
        if ack_silence is not None:
            fields['ack_silence'] = round(ack_silence, 6)
        ack_progress_silence = send_window.ack_progress_silence(now=now)
        if ack_progress_silence is not None:
            fields['ack_progress_silence'] = round(
                ack_progress_silence, 6
            )
        exceeded, distance_info = send_window.distance_exceeded(
            max_window=max_window
        )
        if exceeded:
            (distance, _max_in_flight, effective_cap, unacked,
             distance_limit, last_cum_ack, next_seq) = distance_info
            fields.update({
                'distance': distance,
                'distance_limit': distance_limit,
                'distance_buffered': distance - unacked,
                'distance_unacked': unacked,
                'distance_effective_cap': effective_cap,
                'distance_last_cum_ack': last_cum_ack,
                'distance_next_seq': next_seq,
            })
        if pacer_fields:
            for key, value in pacer_fields.items():
                fields['pacer_' + key] = value
        if self._target_count > 0:
            avg_target = self._target_sum / float(self._target_count)
            fields['pacer_target_inflight_avg'] = round(avg_target, 6)
        if stats_enabled and stats_snapshot:
            if self._summary_last_stats:
                for key, value in stats_snapshot.items():
                    prev = self._summary_last_stats.get(key)
                    if prev is None:
                        continue
                    delta = value - prev
                    if delta:
                        fields['stat_delta_' + key] = delta
            self._summary_last_stats = stats_snapshot
        if self._summary_last_blocked is not None:
            for key, value in self._blocked_counts.items():
                prev = self._summary_last_blocked.get(key, 0)
                delta = value - prev
                if delta:
                    fields['blocked_' + key] = delta
            self._summary_last_blocked = dict(self._blocked_counts)

        self._summary_last_time = now
        self._summary_last_sent = packets_sent
        self._summary_last_recv = packets_received
        self._target_sum = 0.0
        self._target_count = 0
        return fields

    def _should_feedback_adjust(self, prev_target, fields):
        if prev_target is None:
            return False
        target = fields.get('target_inflight')
        if target is None or target >= prev_target:
            return False
        if fields.get('block_penalty'):
            return False
        feedback_target = fields.get('feedback_target')
        base_target = fields.get('base_target')
        baseline_target = fields.get('baseline_target')
        if (feedback_target is None or base_target is None or
                baseline_target is None):
            return False
        if feedback_target >= base_target:
            return False
        if baseline_target >= base_target:
            return False
        return True
