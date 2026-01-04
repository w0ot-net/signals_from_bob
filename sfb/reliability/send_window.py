# -*- coding: ascii -*-
"""
Send window tracking unacked packets.
"""

from __future__ import absolute_import

from collections import OrderedDict, deque

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
        self._ack_history = deque(maxlen=16)
        self._ack_miss_count = 0
        self._ack_miss_last_seq = None
        self._ack_miss_last_is_sack = None
        self._ack_miss_last_time = None
        self._ack_miss_last_ack = None
        self._ack_miss_last_sack = None
        self._last_sack = None
        self._last_sack_ack = None
        self._last_sack_progress_ack = None
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

    def update_sack_progress(self, ack, sack, ack_advanced):
        prev_sack = self._last_sack
        prev_sack_ack = self._last_sack_ack
        self._last_sack = sack
        self._last_sack_ack = ack
        if ack_advanced:
            if sack != 0:
                self._last_sack_progress_ack = ack
            else:
                self._last_sack_progress_ack = None
            return
        if sack != 0:
            if prev_sack_ack != ack or prev_sack != sack:
                self._last_sack_progress_ack = ack

    def sack_progress_ready(self, cum_ack):
        if cum_ack is None:
            return False
        if self._last_sack is None or self._last_sack == 0:
            return False
        if self._last_sack_progress_ack is None:
            return False
        return self._last_sack_progress_ack == cum_ack

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

    def debug_state(self, now=None):
        """
        Return a snapshot of send-window state for logging.
        """
        if now is None:
            now = time_provider.now()
        state = {
            'unacked': len(self._unacked),
            'max_in_flight': self._max_in_flight,
            'next_seq': self._next_seq,
            'retransmit_total': self._retransmit_count,
        }
        keepalive_unacked = 0
        empty_unacked = 0
        data_unacked = 0
        for pkt in self._unacked.values():
            seg_count = len(pkt.segments) if pkt.segments is not None else 0
            if pkt.flags & FLAG_KEEPALIVE:
                keepalive_unacked += 1
            elif seg_count == 0:
                empty_unacked += 1
            else:
                data_unacked += 1
        state['keepalive_unacked'] = keepalive_unacked
        state['empty_unacked'] = empty_unacked
        state['data_unacked'] = data_unacked
        oldest_info = self.get_oldest_unacked_info()
        if oldest_info is not None:
            seq, segments, flags, _encrypted, send_time, retransmit_count = oldest_info
            age = None
            if send_time is not None:
                age = now - send_time
                if age < 0:
                    age = 0.0
                age = round(age, 6)
            state.update({
                'oldest_seq': seq,
                'oldest_age': age,
                'oldest_retransmit_count': retransmit_count,
                'oldest_flags': flags,
                'oldest_seg_count': len(segments) if segments is not None else 0,
            })
        ack_info = self.get_ack_debug_info(now=now)
        if ack_info is not None:
            state.update(ack_info)
        drop_info = self.get_keepalive_drop_info(now=now)
        if drop_info is not None:
            state.update(drop_info)
        return state

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
                seq, now, rtt_samples, is_sack=False, ack=ack, sack=None
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
                    seq, now, rtt_samples, is_sack=True, ack=ack, sack=sack
                )
                acked_count += acked_delta
                data_acked_count += data_acked_delta
        return (acked_count, data_acked_count)

    def _ack_seq(self, seq, now, rtt_samples, is_sack, ack=None, sack=None):
        pkt = self._unacked.pop(seq, None)
        if pkt is None:
            self._record_ack_miss(seq, is_sack, now, ack, sack)
            return (0, 0)
        self._record_ack_event(seq, pkt, now, is_sack, ack, sack)
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

    def get_ack_debug_info(self, seq=None, now=None):
        if now is None:
            now = time_provider.now()
        info = {
            'ack_history_len': len(self._ack_history),
            'ack_miss_count': self._ack_miss_count,
            'ack_miss_last_seq': self._ack_miss_last_seq,
            'ack_miss_last_is_sack': self._ack_miss_last_is_sack,
            'ack_miss_last_ack': self._ack_miss_last_ack,
            'ack_miss_last_sack': self._ack_miss_last_sack,
        }
        if self._ack_miss_last_time is not None:
            miss_age = now - self._ack_miss_last_time
            if miss_age < 0:
                miss_age = 0.0
            info['ack_miss_last_age'] = round(miss_age, 6)
        last = self._ack_history[-1] if self._ack_history else None
        if last is not None:
            info['ack_history_last_seq'] = last['seq']
            info['ack_history_last_is_sack'] = last['is_sack']
            info['ack_history_last_ack'] = last['ack']
            info['ack_history_last_sack'] = last['sack']
            info['ack_history_last_flags'] = last['flags']
            info['ack_history_last_seg_count'] = last['seg_count']
            info['ack_history_last_retransmit_count'] = (
                last['retransmit_count']
            )
            if last.get('acked_time') is not None:
                age = now - last['acked_time']
                if age < 0:
                    age = 0.0
                info['ack_history_last_age'] = round(age, 6)
        if seq is not None:
            missing = None
            for item in self._ack_history:
                if item['seq'] == seq:
                    missing = item
                    break
            info['ack_history_missing_hit'] = missing is not None
            if missing is not None:
                info['ack_history_missing_is_sack'] = missing['is_sack']
                info['ack_history_missing_ack'] = missing['ack']
                info['ack_history_missing_sack'] = missing['sack']
                info['ack_history_missing_flags'] = missing['flags']
                info['ack_history_missing_seg_count'] = missing['seg_count']
                info['ack_history_missing_retransmit_count'] = (
                    missing['retransmit_count']
                )
                if missing.get('acked_time') is not None:
                    age = now - missing['acked_time']
                    if age < 0:
                        age = 0.0
                    info['ack_history_missing_age'] = round(age, 6)
        return info

    def _record_ack_event(self, seq, pkt, now, is_sack, ack, sack):
        entry = {
            'seq': seq,
            'is_sack': is_sack,
            'ack': ack,
            'sack': sack,
            'flags': pkt.flags,
            'seg_count': len(pkt.segments) if pkt.segments is not None else 0,
            'retransmit_count': pkt.retransmit_count,
            'acked_time': now,
        }
        self._ack_history.append(entry)

    def _record_ack_miss(self, seq, is_sack, now, ack, sack):
        self._ack_miss_count += 1
        self._ack_miss_last_seq = seq
        self._ack_miss_last_is_sack = is_sack
        self._ack_miss_last_time = now
        self._ack_miss_last_ack = ack
        self._ack_miss_last_sack = sack


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
