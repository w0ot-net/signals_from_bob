# -*- coding: ascii -*-
"""
Send window tracking unacked packets.
"""

from __future__ import absolute_import

from collections import OrderedDict

from .. import time_provider
from ..protocol import (
    seq_lt,
    seq_diff,
    SEQ_MAX,
    SACK_BITS,
    MAX_IN_FLIGHT,
    FLAG_KEEPALIVE,
)
from .stats import NoopReliabilityStats


class SendWindow(object):
    """
    Send window tracking unacked packets.

    Tracks packets that have been sent but not yet acknowledged.
    Handles retransmission timing and SACK processing.

    Stores segments and encrypted body so packets can be rebuilt
    with fresh ACK/SACK while reusing ciphertext on retransmit.
    """

    def __init__(self, max_in_flight, stats=None):
        if max_in_flight > MAX_IN_FLIGHT:
            raise ValueError('max_in_flight cannot exceed %d' % MAX_IN_FLIGHT)
        self._max_in_flight = max_in_flight
        self._next_seq = 0
        # Ordered by initial send; seq reuse cannot overlap while
        # max_in_flight << 2^16.
        self._unacked = OrderedDict()  # seq -> _UnackedPacket
        self._retransmit_count = 0  # Total retransmits
        self._stats = stats or NoopReliabilityStats()
        self._last_keepalive_drop_seq = None
        self._last_keepalive_drop_time = None
        self._last_keepalive_drop_reason = None
        self._last_keepalive_drop_unacked_before = None
        self._last_keepalive_drop_unacked_after = None
        self._keepalive_drop_count = 0

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

    def send(self, segments, flags=0, encrypted_body=None, now=None):
        """
        Record a packet being sent.

        Args:
            segments: List of Segment instances to store for retransmit
            flags: Packet flags to preserve for retransmit
            encrypted_body: Cached encrypted body for retransmit

        Returns:
            int: Sequence number assigned to this packet
        """
        if now is None:
            now = time_provider.now()

        if not self.can_send:
            raise ValueError('Send window full')

        seq = self._next_seq
        self._next_seq = (self._next_seq + 1) & SEQ_MAX

        self._unacked[seq] = _UnackedPacket(
            seq=seq,
            segments=segments,
            flags=flags,
            encrypted_body=encrypted_body,
            send_time=now,
            retransmit_count=0,
        )
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
            now = time_provider.now()

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

    def get_retransmits(self, rto_sec, now=None, max_count=None):
        """
        Get packets that need retransmission (Alice, timer-driven).

        Returns in send_time order (oldest first) for consistent behavior.

        Returns:
            list: List of (seq, segments, flags, encrypted_body) to retransmit
        """
        if now is None:
            now = time_provider.now()

        if max_count is not None and max_count <= 0:
            return []

        retransmits = []
        for seq, pkt in self._unacked.items():
            if now - pkt.send_time >= rto_sec:
                retransmits.append(
                    (pkt.send_time, seq, pkt)
                )

        if not retransmits:
            return []

        retransmits.sort(key=lambda item: (item[0], item[1]))
        if max_count is not None:
            retransmits = retransmits[:max_count]

        return [
            (seq, pkt.segments, pkt.flags, pkt.encrypted_body)
            for _, seq, pkt in retransmits
        ]

    def get_oldest_unacked(self):
        """
        Get oldest unacked packet for retransmission (Bob, opportunity-driven).

        Returns:
            tuple: (seq, segments, flags, encrypted_body) or None if no unacked packets
        """
        oldest = self._select_oldest_unacked()
        if oldest is None:
            return None
        seq, pkt = oldest
        return (seq, pkt.segments, pkt.flags, pkt.encrypted_body)

    def get_oldest_unacked_info(self):
        """
        Get oldest unacked packet with timing info (Bob, opportunity-driven).

        Returns:
            tuple: (seq, segments, flags, encrypted_body, send_time, retransmit_count) or None
        """
        oldest = self._select_oldest_unacked()
        if oldest is None:
            return None
        seq, pkt = oldest
        return (
            seq,
            pkt.segments,
            pkt.flags,
            pkt.encrypted_body,
            pkt.send_time,
            pkt.retransmit_count,
        )

    def get_unacked_info(self, seq):
        """
        Get unacked packet data for a specific sequence number.

        Returns:
            tuple: (seq, segments, flags, encrypted_body, send_time, retransmit_count)
                or None if not found.
        """
        pkt = self._unacked.get(seq)
        if pkt is None:
            return None
        return (
            seq,
            pkt.segments,
            pkt.flags,
            pkt.encrypted_body,
            pkt.send_time,
            pkt.retransmit_count,
        )

    def mark_retransmit(self, seq, now=None):
        """
        Mark a packet as retransmitted.
        """
        if now is None:
            now = time_provider.now()

        pkt = self._unacked.get(seq)
        if pkt is None:
            return

        pkt.retransmit_count += 1
        pkt.send_time = now
        self._retransmit_count += 1
        self._stats.on_retransmit()

    def drop_keepalive(self, seq, reason=None, now=None):
        """
        Drop an unacked keepalive-only packet.
        """
        if now is None:
            now = time_provider.now()
        pkt = self._unacked.get(seq)
        if pkt is None:
            return False
        if not (pkt.flags & FLAG_KEEPALIVE):
            return False
        count_before = len(self._unacked)
        del self._unacked[seq]
        count_after = len(self._unacked)
        self._record_keepalive_drop(
            seq, reason, now, count_before, count_after
        )
        return True

    def drop_oldest_keepalive(self, reason=None, now=None):
        """
        Drop the oldest unacked keepalive-only packet.
        """
        if now is None:
            now = time_provider.now()
        for seq, pkt in self._unacked.items():
            if pkt.flags & FLAG_KEEPALIVE:
                count_before = len(self._unacked)
                del self._unacked[seq]
                count_after = len(self._unacked)
                self._record_keepalive_drop(
                    seq, reason, now, count_before, count_after
                )
                return seq
        return None

    def get_unacked_in_sack_window(self, ack, max_offset=None):
        """
        Return unacked seq numbers within the SACK window, ordered by offset.
        """
        if max_offset is None:
            max_offset = SACK_BITS
        candidates = []
        for seq in self._unacked:
            diff = seq_diff(seq, ack)
            if diff < 0 or diff > max_offset:
                continue
            candidates.append((diff, seq))
        candidates.sort()
        return [seq for _, seq in candidates]

    def get_keepalive_drop_info(self, now=None):
        if self._last_keepalive_drop_seq is None:
            return None
        if now is None:
            now = time_provider.now()
        age = None
        if self._last_keepalive_drop_time is not None:
            age = now - self._last_keepalive_drop_time
            if age < 0:
                age = 0.0
            age = round(age, 6)
        return {
            'keepalive_drop_seq': self._last_keepalive_drop_seq,
            'keepalive_drop_reason': self._last_keepalive_drop_reason,
            'keepalive_drop_age': age,
            'keepalive_drop_unacked_before': self._last_keepalive_drop_unacked_before,
            'keepalive_drop_unacked_after': self._last_keepalive_drop_unacked_after,
            'keepalive_drop_count': self._keepalive_drop_count,
        }

    def _record_keepalive_drop(self, seq, reason, now, count_before, count_after):
        self._last_keepalive_drop_seq = seq
        self._last_keepalive_drop_reason = reason
        self._last_keepalive_drop_time = now
        self._last_keepalive_drop_unacked_before = count_before
        self._last_keepalive_drop_unacked_after = count_after
        self._keepalive_drop_count += 1

    def _ack_cumulative(self, ack, now, rtt_samples):
        acked_count = 0
        data_acked_count = 0
        while self._unacked:
            seq = next(iter(self._unacked))
            if not seq_lt(seq, ack):
                break
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
            self._stats.on_ack_first_tx()
            if not (pkt.flags & FLAG_KEEPALIVE):
                rtt_ms = (now - pkt.send_time) * 1000
                rtt_samples.append(rtt_ms)
                self._stats.on_rtt_sample()
        data_acked = 1 if pkt.segments else 0
        return (1, data_acked)

    def _select_oldest_unacked(self):
        if not self._unacked:
            return None
        oldest = None
        oldest_time = None
        for seq, pkt in self._unacked.items():
            pkt_time = pkt.send_time
            if pkt_time is None:
                pkt_time = 0.0
            if oldest is None or pkt_time < oldest_time:
                oldest = (seq, pkt)
                oldest_time = pkt_time
        return oldest


class _UnackedPacket(object):
    """Tracking data for an unacked packet."""

    __slots__ = (
        'seq',
        'segments',
        'flags',
        'encrypted_body',
        'send_time',
        'retransmit_count',
    )

    def __init__(self, seq, segments, flags, encrypted_body, send_time, retransmit_count):
        self.seq = seq
        self.segments = segments
        self.flags = flags
        self.encrypted_body = encrypted_body
        self.send_time = send_time
        self.retransmit_count = retransmit_count
