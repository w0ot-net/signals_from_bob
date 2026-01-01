# -*- coding: ascii -*-
"""
Adaptive pacing for Alice sends.
"""

from __future__ import absolute_import


class AdaptivePacer(object):
    """
    Adaptive pacing controller driven by RTT EWMA and inflight targets.
    """

    def __init__(self, enabled, target_inflight_ratio, min_inflight,
                 max_inflight, rtt_floor_ms, fast_start=True,
                 time_based=False):
        self._enabled = bool(enabled)
        self._target_ratio = float(target_inflight_ratio)
        self._min_inflight = int(min_inflight)
        self._max_inflight = int(max_inflight) if max_inflight is not None else None
        self._rtt_floor_ms = float(rtt_floor_ms)
        self._fast_start_enabled = bool(fast_start)
        self._time_based = bool(time_based)
        self._fast_start_active = False
        self._last_send_time = None

    @property
    def enabled(self):
        return self._enabled

    @property
    def fast_start_active(self):
        return self._fast_start_active

    def set_fast_start(self):
        if self._enabled and self._fast_start_enabled:
            self._fast_start_active = True

    def target_inflight(self, cap):
        if cap < 1:
            cap = 1
        target = int(cap * self._target_ratio)
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

    def _rtt_sec(self, srtt_ms):
        if srtt_ms is None:
            rtt_ms = self._rtt_floor_ms
        else:
            rtt_ms = srtt_ms
            if rtt_ms < self._rtt_floor_ms:
                rtt_ms = self._rtt_floor_ms
        return rtt_ms / 1000.0

    def can_send(self, now, unacked_count, cap, srtt_ms=None):
        if not self._enabled:
            return True
        target = self.target_inflight(cap)
        if unacked_count >= target:
            return False
        if self._fast_start_active and self._fast_start_enabled:
            return True
        if not self._time_based:
            return True
        if self._last_send_time is None:
            return True
        interval = self._rtt_sec(srtt_ms) / float(target)
        if interval <= 0:
            return True
        return (now - self._last_send_time) >= interval

    def on_send(self, now, unacked_count, cap, srtt_ms=None, is_keepalive=False):
        if not self._enabled or is_keepalive:
            return
        self._last_send_time = now
        if not self._fast_start_enabled:
            return
        if self._fast_start_active:
            target = self.target_inflight(cap)
            if unacked_count + 1 >= target:
                self._fast_start_active = False

    def on_response(self, has_real_data):
        if has_real_data:
            self.set_fast_start()

    def state_fields(self, unacked_count, cap, srtt_ms=None, rate_limit=None):
        fields = {
            'srtt_ms': srtt_ms,
            'target_inflight': self.target_inflight(cap),
            'unacked_count': unacked_count,
            'cap': cap,
            'fast_start': self._fast_start_active,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
