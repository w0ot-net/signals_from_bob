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
from .stats import NoopReliabilityStats


class SendWindow(object):
    """
    Send window tracking unacked packets.

    Tracks packets that have been sent but not yet acknowledged.
    Handles retransmission timing and SACK processing.

    Stores segments (not encoded bytes) so packets can be rebuilt
    with fresh ACK/SACK on retransmit.
    """

    def __init__(self, max_in_flight=DEFAULT_MAX_IN_FLIGHT, stats=None):
        if max_in_flight > MAX_IN_FLIGHT:
            raise ValueError('max_in_flight cannot exceed %d' % MAX_IN_FLIGHT)
        self._max_in_flight = max_in_flight
        self._next_seq = 0
        self._unacked = {}  # seq -> _UnackedPacket
        self._send_order = deque()
        self._retransmit_count = 0  # Total retransmits
        self._stats = stats or NoopReliabilityStats()

    @property
    def next_seq(self):
        """Next sequence number to use."""
        return self._next_seq

    @property
    def can_send(self):
        """True if window has room for a new packet."""
        return len(self._unacked) < self._max_in_flight

    @property
    def unacked_count(self):
        """Number of unacked packets."""
        return len(self._unacked)

    def send(self, segments, flags=0, now=None):
        """
        Record a packet being sent.

        Args:
            segments: List of Segment instances to store for retransmit
            flags: Packet flags to preserve for retransmit

        Returns:
            int: Sequence number assigned to this packet
        """
        if now is None:
            now = time.time()

        if not self.can_send:
            raise ValueError('Send window full')

        seq = self._next_seq
        self._next_seq = (self._next_seq + 1) & SEQ_MAX

        self._unacked[seq] = _UnackedPacket(
            seq=seq,
            segments=segments,
            flags=flags,
            send_time=now,
            retransmit_count=0,
        )
        self._send_order.append(seq)
        self._stats.on_send()

        return seq

    def process_ack(self, ack, sack, now=None):
        """
        Process incoming ACK and SACK.

        Returns:
            tuple: (rtt_samples, acked_count, data_acked_count)
                rtt_samples: list of RTT samples in ms for first-TX packets
                acked_count: count of newly acked packets (all acks)
                data_acked_count: count of newly acked packets with segments
        """
        if now is None:
            now = time.time()

        rtt_samples = []
        acked_count = 0
        data_acked_count = 0
        acked_delta, data_acked_delta = self._ack_cumulative(
            ack, now, rtt_samples
        )
        acked_count += acked_delta
        data_acked_count += data_acked_delta
        acked_delta, data_acked_delta = self._ack_sack(
            ack, sack, now, rtt_samples
        )
        acked_count += acked_delta
        data_acked_count += data_acked_delta

        return (rtt_samples, acked_count, data_acked_count)

    def get_retransmits(self, rto_sec, now=None):
        """
        Get packets that need retransmission (Alice, timer-driven).

        Returns in send order (oldest first) for consistent behavior.

        Returns:
            list: List of (seq, segments, flags) to retransmit
        """
        if now is None:
            now = time.time()

        retransmits = []
        for seq in self._send_order:
            pkt = self._unacked.get(seq)
            if pkt is not None and now - pkt.send_time >= rto_sec:
                retransmits.append((seq, pkt.segments, pkt.flags))

        return retransmits

    def get_oldest_unacked(self):
        """
        Get oldest unacked packet for retransmission (Bob, opportunity-driven).

        Returns:
            tuple: (seq, segments, flags) or None if no unacked packets
        """
        if not self._unacked:
            return None

        while self._send_order:
            seq = self._send_order[0]
            pkt = self._unacked.get(seq)
            if pkt is not None:
                return (seq, pkt.segments, pkt.flags)
            self._send_order.popleft()
        return None

    def get_oldest_unacked_info(self):
        """
        Get oldest unacked packet with timing info (Bob, opportunity-driven).

        Returns:
            tuple: (seq, segments, flags, send_time, retransmit_count) or None
        """
        if not self._unacked:
            return None

        while self._send_order:
            seq = self._send_order[0]
            pkt = self._unacked.get(seq)
            if pkt is not None:
                return (
                    seq,
                    pkt.segments,
                    pkt.flags,
                    pkt.send_time,
                    pkt.retransmit_count,
                )
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

        pkt.retransmit_count += 1
        pkt.send_time = now
        self._retransmit_count += 1
        self._stats.on_retransmit()

    def _ack_cumulative(self, ack, now, rtt_samples):
        acked_count = 0
        data_acked_count = 0
        while self._send_order:
            seq = self._send_order[0]
            if not seq_lt(seq, ack):
                break
            self._send_order.popleft()
            acked_delta, data_acked_delta = self._ack_seq(
                seq, now, rtt_samples, is_sack=False
            )
            acked_count += acked_delta
            data_acked_count += data_acked_delta
        return (acked_count, data_acked_count)

    def _ack_sack(self, ack, sack, now, rtt_samples):
        if sack == 0:
            return (0, 0)
        acked_count = 0
        data_acked_count = 0
        for offset in range(1, SACK_BITS + 1):
            if sack & (1 << (offset - 1)):
                seq = (ack + offset) & SEQ_MAX
                acked_delta, data_acked_delta = self._ack_seq(
                    seq, now, rtt_samples, is_sack=True
                )
                acked_count += acked_delta
                data_acked_count += data_acked_delta
        return (acked_count, data_acked_count)

    def _ack_seq(self, seq, now, rtt_samples, is_sack):
        pkt = self._unacked.pop(seq, None)
        if pkt is None:
            return (0, 0)
        self._stats.on_ack(is_sack)
        if pkt.retransmit_count == 0:
            rtt_ms = (now - pkt.send_time) * 1000
            rtt_samples.append(rtt_ms)
            self._stats.on_ack_first_tx()
            self._stats.on_rtt_sample()
        data_acked = 1 if pkt.segments else 0
        return (1, data_acked)


class _UnackedPacket(object):
    """Tracking data for an unacked packet."""

    __slots__ = ('seq', 'segments', 'flags', 'send_time', 'retransmit_count')

    def __init__(self, seq, segments, flags, send_time, retransmit_count):
        self.seq = seq
        self.segments = segments
        self.flags = flags
        self.send_time = send_time
        self.retransmit_count = retransmit_count
