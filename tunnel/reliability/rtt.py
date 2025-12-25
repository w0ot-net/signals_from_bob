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

    def __init__(self):
        self._srtt = None
        self._rto = DEFAULT_RTO_MS
        self._backoff_count = 0

    @property
    def rto_ms(self):
        """Current RTO in milliseconds."""
        return self._rto

    @property
    def rto_sec(self):
        """Current RTO in seconds."""
        return self._rto / 1000.0

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

    @staticmethod
    def _clamp(rto):
        if rto < MIN_RTO_MS:
            return MIN_RTO_MS
        if rto > MAX_RTO_MS:
            return MAX_RTO_MS
        return rto
