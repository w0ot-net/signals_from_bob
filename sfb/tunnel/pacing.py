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
                 max_inflight):
        self._enabled = bool(enabled)
        self._target_ratio = float(target_inflight_ratio)
        self._min_inflight = int(min_inflight)
        self._max_inflight = int(max_inflight) if max_inflight is not None else None

    @property
    def enabled(self):
        return self._enabled

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

    def can_send(self, unacked_count, cap):
        if not self._enabled:
            return True
        target = self.target_inflight(cap)
        return unacked_count < target

    def state_fields(self, unacked_count, cap, rate_limit=None):
        fields = {
            'target_inflight': self.target_inflight(cap),
            'unacked_count': unacked_count,
            'cap': cap,
        }
        if rate_limit is not None:
            fields['rate_limit'] = rate_limit
        return fields
