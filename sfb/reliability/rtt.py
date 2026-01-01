# -*- coding: ascii -*-
"""
RTT estimation for retransmission timeout calculation.
"""

from __future__ import absolute_import

from ..protocol import (
    DEFAULT_RTO_MS,
    MIN_RTO_MS,
    MAX_RTO_MS,
)


class RttEstimator(object):
    """
    RTT estimator for computing retransmission timeout.

    Uses exponentially weighted moving average (EWMA) with Karn's algorithm.
    Only used by Alice; Bob does not track RTT.
    """

    ALPHA = 0.125  # weight for new sample
    BETA = 0.875   # weight for old srtt (1 - ALPHA)

    def __init__(self, initial_rto_ms=None, min_rto_ms=None, max_rto_ms=None):
        self._srtt = None
        self._initial_rto = initial_rto_ms if initial_rto_ms is not None else DEFAULT_RTO_MS
        self._min_rto = min_rto_ms if min_rto_ms is not None else MIN_RTO_MS
        self._max_rto = max_rto_ms if max_rto_ms is not None else MAX_RTO_MS
        self._rto = self._initial_rto
        self._backoff_count = 0

    @property
    def rto_ms(self):
        """Current RTO in milliseconds."""
        return self._rto

    @property
    def rto_sec(self):
        """Current RTO in seconds."""
        return self._rto / 1000.0

    @property
    def srtt_ms(self):
        """Smoothed RTT (EWMA) in milliseconds, or None if unset."""
        return self._srtt

    def add_sample(self, rtt_ms):
        """
        Add an RTT sample and update RTO.

        Only call this for packets acked on first transmission (Karn's rule).
        """
        if self._srtt is None:
            self._srtt = rtt_ms
        else:
            self._srtt = self.BETA * self._srtt + self.ALPHA * rtt_ms

        self._rto = self._clamp(self._srtt * 2)
        self._backoff_count = 0

    def backoff(self):
        """Double the RTO for exponential backoff."""
        self._backoff_count += 1
        self._rto = self._clamp(self._rto * 2)

    def reset_backoff(self):
        """Reset backoff after successful transmission."""
        self._backoff_count = 0
        if self._srtt is not None:
            self._rto = self._clamp(self._srtt * 2)

    def _clamp(self, rto):
        if rto < self._min_rto:
            return self._min_rto
        if rto > self._max_rto:
            return self._max_rto
        return rto
