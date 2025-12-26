# -*- coding: ascii -*-
"""
Receive window for ordering and deduplication.
"""

from __future__ import absolute_import

from ..protocol import (
    seq_lt,
    seq_diff,
    SEQ_MAX,
    SACK_BITS,
    MAX_IN_FLIGHT,
)
from .stats import NoopReliabilityStats


class RecvWindow(object):
    """
    Receive window for ordering and deduplication.

    Buffers out-of-order packets and releases them in order.
    Computes cumulative ACK and SACK bitmap.
    """

    def __init__(self, max_buffer=MAX_IN_FLIGHT, stats=None):
        self._next_expected = 0
        self._buffer = {}  # seq -> packet_data (out-of-order)
        self._max_buffer = max_buffer
        self._stats = stats or NoopReliabilityStats()

    @property
    def ack(self):
        """Cumulative acknowledgment (next expected sequence number)."""
        return self._next_expected

    @property
    def sack(self):
        """SACK bitmap for packets beyond ack."""
        bitmap = 0
        for seq in self._buffer:
            offset = seq_diff(seq, self._next_expected)
            if 1 <= offset <= SACK_BITS:
                bitmap |= (1 << (offset - 1))
        return bitmap

    def receive(self, seq, packet_data):
        """
        Process a received packet.

        Returns:
            list: List of (seq, packet_data) in order, ready for delivery.
        """
        # Reject packets already received
        if seq_lt(seq, self._next_expected):
            self._stats.on_recv_duplicate()
            return []

        # Reject packets beyond SACK window (can't represent in SACK bitmap)
        offset = seq_diff(seq, self._next_expected)
        if offset > SACK_BITS:
            self._stats.on_recv_out_of_window()
            return []

        # Reject duplicates
        if seq in self._buffer:
            self._stats.on_recv_duplicate()
            return []

        if seq == self._next_expected:
            ready = [(seq, packet_data)]
            self._next_expected = (self._next_expected + 1) & SEQ_MAX

            while self._next_expected in self._buffer:
                buffered = self._buffer.pop(self._next_expected)
                ready.append((self._next_expected, buffered))
                self._next_expected = (self._next_expected + 1) & SEQ_MAX

            self._stats.on_recv_delivered(len(ready))
            return ready

        if len(self._buffer) >= self._max_buffer:
            self._stats.on_recv_buffer_full()
            return []

        self._buffer[seq] = packet_data
        self._stats.on_recv_buffered()
        return []

    def set_initial_seq(self, seq):
        """
        Set the initial expected sequence number.
        """
        self._next_expected = seq
        self._buffer.clear()

    def set_max_buffer(self, max_buffer):
        """
        Update buffer limit (e.g., after window negotiation).
        """
        if max_buffer > MAX_IN_FLIGHT:
            raise ValueError('max_buffer cannot exceed %d' % MAX_IN_FLIGHT)
        self._max_buffer = max_buffer
        if len(self._buffer) <= max_buffer:
            return
        self._trim_buffer()

    def _trim_buffer(self):
        if len(self._buffer) <= self._max_buffer:
            return
        entries = []
        for seq in self._buffer:
            offset = seq_diff(seq, self._next_expected)
            entries.append((offset, seq))
        entries.sort(reverse=True)
        for _, seq in entries:
            if len(self._buffer) <= self._max_buffer:
                break
            self._buffer.pop(seq, None)
