# -*- coding: ascii -*-
"""
Send window tracking unacked packets.
"""

from __future__ import absolute_import

import time
from collections import deque

from ..protocol import (
    seq_lt,
    SEQ_MAX,
    SACK_BITS,
    MAX_IN_FLIGHT,
    DEFAULT_MAX_IN_FLIGHT,
)


class SendWindow(object):
    """
    Send window tracking unacked packets.

    Tracks packets that have been sent but not yet acknowledged.
    Handles retransmission timing and SACK processing.
    """

    def __init__(self, max_in_flight=DEFAULT_MAX_IN_FLIGHT):
        if max_in_flight > MAX_IN_FLIGHT:
            raise ValueError('max_in_flight cannot exceed %d' % MAX_IN_FLIGHT)
        self._max_in_flight = max_in_flight
        self._next_seq = 0
        self._unacked = {}  # seq -> _UnackedPacket
        self._send_order = deque()
        self._new_in_flight = 0  # Count of new (non-retransmit) packets

    @property
    def next_seq(self):
        """Next sequence number to use."""
        return self._next_seq

    @property
    def can_send(self):
        """True if window has room for a new packet."""
        return self._new_in_flight < self._max_in_flight

    @property
    def unacked_count(self):
        """Number of unacked packets."""
        return len(self._unacked)

    def send(self, packet_data, now=None):
        """
        Record a packet being sent.

        Returns:
            int: Sequence number assigned to this packet
        """
        if now is None:
            now = time.time()

        seq = self._next_seq
        self._next_seq = (self._next_seq + 1) & SEQ_MAX

        self._unacked[seq] = _UnackedPacket(
            seq=seq,
            data=packet_data,
            send_time=now,
            retransmit_count=0,
        )
        self._send_order.append(seq)
        self._new_in_flight += 1

        return seq

    def process_ack(self, ack, sack, now=None):
        """
        Process incoming ACK and SACK.

        Returns:
            list: RTT samples in ms for packets acked on first transmission
        """
        if now is None:
            now = time.time()

        rtt_samples = []
        self._ack_cumulative(ack, now, rtt_samples)
        self._ack_sack(ack, sack, now, rtt_samples)

        return rtt_samples

    def get_retransmits(self, rto_sec, now=None):
        """
        Get packets that need retransmission (Alice, timer-driven).

        Returns:
            list: List of (seq, packet_data) to retransmit
        """
        if now is None:
            now = time.time()

        retransmits = []
        for seq, pkt in self._unacked.items():
            if now - pkt.send_time >= rto_sec:
                retransmits.append((seq, pkt.data))

        return retransmits

    def get_oldest_unacked(self):
        """
        Get oldest unacked packet for retransmission (Bob, opportunity-driven).

        Returns:
            tuple: (seq, packet_data) or None if no unacked packets
        """
        if not self._unacked:
            return None

        while self._send_order:
            seq = self._send_order[0]
            pkt = self._unacked.get(seq)
            if pkt is not None:
                return (seq, pkt.data)
            self._send_order.popleft()
        return None

    def mark_retransmit(self, seq, now=None):
        """
        Mark a packet as retransmitted.
        """
        if now is None:
            now = time.time()

        pkt = self._unacked.get(seq)
        if pkt is None:
            return

        if pkt.retransmit_count == 0:
            self._new_in_flight -= 1
        pkt.retransmit_count += 1
        pkt.send_time = now

    def _ack_cumulative(self, ack, now, rtt_samples):
        while self._send_order:
            seq = self._send_order[0]
            if not seq_lt(seq, ack):
                break
            self._send_order.popleft()
            self._ack_seq(seq, now, rtt_samples)

    def _ack_sack(self, ack, sack, now, rtt_samples):
        if sack == 0:
            return
        for offset in range(1, SACK_BITS + 1):
            if sack & (1 << (offset - 1)):
                seq = (ack + offset) & SEQ_MAX
                self._ack_seq(seq, now, rtt_samples)

    def _ack_seq(self, seq, now, rtt_samples):
        pkt = self._unacked.pop(seq, None)
        if pkt is None:
            return
        if pkt.retransmit_count == 0:
            self._new_in_flight -= 1
            rtt_ms = (now - pkt.send_time) * 1000
            rtt_samples.append(rtt_ms)


class _UnackedPacket(object):
    """Tracking data for an unacked packet."""

    __slots__ = ('seq', 'data', 'send_time', 'retransmit_count')

    def __init__(self, seq, data, send_time, retransmit_count):
        self.seq = seq
        self.data = data
        self.send_time = send_time
        self.retransmit_count = retransmit_count
