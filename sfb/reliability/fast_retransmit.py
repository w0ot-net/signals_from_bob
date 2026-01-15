# -*- coding: ascii -*-
"""
Fast retransmit selection for Alice.
"""

from __future__ import absolute_import


class FastRetransmitController(object):
    """
    Track and select fast retransmit candidates.
    """

    _MIN_BUFFERED = 1

    def __init__(self, send_window, rtt, enabled, min_age_ratio,
                 max_per_seq, min_rto_ms):
        self._send_window = send_window
        self._rtt = rtt
        self._enabled = bool(enabled)
        self._min_age_ratio = float(min_age_ratio)
        self._max_per_seq = int(max_per_seq)
        if min_rto_ms is None:
            self._min_rto_sec = 0.0
        else:
            self._min_rto_sec = float(min_rto_ms) / 1000.0
        self._counts = {}

    @property
    def enabled(self):
        return self._enabled

    @property
    def min_age_ratio(self):
        return self._min_age_ratio

    def prune(self):
        if not self._counts:
            return
        if self._send_window.unacked_count == 0:
            self._counts.clear()
            return
        valid = set(self._send_window.unacked_seqs())
        stale = []
        for seq in self._counts:
            if seq not in valid:
                stale.append(seq)
        for seq in stale:
            del self._counts[seq]

    def note_sent(self, seq):
        self._counts[seq] = self._counts.get(seq, 0) + 1

    def select_candidate(self, now, ack_silence, max_window, cap_override=None):
        if not self._enabled:
            return None
        if ack_silence is None:
            return None
        if ack_silence >= self._rtt.rto_sec:
            return None
        if not self._send_window.sack_progress_ready():
            return None
        hole_state = self._send_window.sack_hole_state(now=now)
        if hole_state is None:
            return None
        if not hole_state.get('missing_in_unacked'):
            return None
        buffered = hole_state.get('buffered')
        if buffered is None or buffered < self._MIN_BUFFERED:
            return None
        last_cum_ack = hole_state.get('last_cum_ack')
        if last_cum_ack is None:
            return None
        missing_info = self._send_window.get_unacked_info(last_cum_ack)
        if missing_info is None:
            return None
        (seq, segments, flags, encrypted_body,
         send_time, _retransmit_count) = missing_info
        missing_age = hole_state.get('missing_age')
        if missing_age is None:
            return None
        count = self._counts.get(seq, 0)
        min_age = self._rtt.rto_sec * self._min_age_ratio
        min_rto_sec = self._min_rto_sec
        if min_rto_sec > 0 and min_age > min_rto_sec:
            min_age = min_rto_sec
        if count >= self._max_per_seq:
            min_age *= (count - self._max_per_seq + 2)
        if missing_age < min_age:
            return None
        return (seq, segments, flags, encrypted_body, send_time)
