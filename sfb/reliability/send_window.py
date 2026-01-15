# -*- coding: ascii -*-
"""
Send window tracking unacked packets.
"""

from __future__ import absolute_import

from collections import OrderedDict, deque
import heapq
from ..protocol import (
    seq_lt,
    seq_gt,
    seq_diff,
    SEQ_MAX,
    SACK_BITS,
    MAX_IN_FLIGHT,
    FLAG_KEEPALIVE,
)
from .stats import NoopReliabilityStats


class SendWindowError(Exception):
    """Send-window invariant violation."""

    def __init__(self, message, seq=None, context=None):
        Exception.__init__(self, message)
        self.seq = seq
        self.context = context


class SendWindow(object):
    """
    Send window tracking unacked packets.

    Tracks packets that have been sent but not yet acknowledged.
    Handles retransmission timing and SACK processing.

    Stores segments and encrypted body so packets can be rebuilt
    with fresh ACK/SACK while reusing ciphertext on retransmit.

    Callers must supply a monotonic tick time for all time-based operations.
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
        self._last_cum_ack = None
        self._last_cum_ack_time = None
        self._last_ack_progress_time = None
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
        self._data_unacked_count = 0
        self._unacked_heap = []
        self._unacked_heap_token = 0
        self._unacked_heap_enabled = False

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

    def unacked_seqs(self):
        """Return list of unacked sequence numbers."""
        return list(self._unacked.keys())

    def data_unacked_count(self):
        """Number of unacked packets carrying segments."""
        return self._data_unacked_count

    @property
    def last_cum_ack(self):
        """Last cumulative ACK observed from peer, or None."""
        return self._last_cum_ack

    @property
    def last_cum_ack_time(self):
        """Time of last cumulative ACK advance, or None."""
        return self._last_cum_ack_time

    @property
    def last_ack_progress_time(self):
        """Time when unacked count last decreased, or None."""
        return self._last_ack_progress_time

    def _require_now(self, now, context):
        if now is None:
            raise SendWindowError(
                'Send window missing now',
                context=context,
            )

    def ack_silence(self, now=None):
        """Seconds since last cumulative ACK advance, or None."""
        self._require_now(now, 'ack_silence')
        if self._last_cum_ack_time is None:
            return None
        silence = now - self._last_cum_ack_time
        return silence

    def ack_progress_silence(self, now=None):
        """Seconds since last ACK progress (unacked decreased), or None."""
        self._require_now(now, 'ack_progress_silence')
        if self._last_ack_progress_time is None:
            return None
        silence = now - self._last_ack_progress_time
        return silence

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
        if not self.can_send:
            raise ValueError('Send window full')

        seq = self._next_seq
        if now is None:
            raise SendWindowError(
                'Send window missing send_time',
                seq=seq,
                context='send',
            )
        self._next_seq = (self._next_seq + 1) & SEQ_MAX

        self._unacked[seq] = _UnackedPacket(
            seq=seq,
            segments=segments,
            flags=flags,
            encrypted_body=encrypted_body,
            send_time=now,
            first_send_time=now,
            retransmit_count=0,
        )
        if segments:
            self._data_unacked_count += 1
        if self._unacked_heap_enabled:
            self._push_unacked_heap(self._unacked[seq])
        self._stats.on_send()

        return seq

    def process_ack_with_progress(self, ack, sack, now=None, sample_rtt=True):
        """
        Update ACK tracking, SACK progress, and process ACK/SACK.

        Returns:
            tuple: (rtt_samples, acked_count, data_acked_count, unacked_before,
                unacked_after, prev_cum_ack, prev_cum_ack_time, ack_advanced,
                ack_progressed)
        """
        self._require_now(now, 'process_ack_with_progress')
        if self._ack_is_future(ack):
            unacked = len(self._unacked)
            return (
                [],
                0,
                0,
                unacked,
                unacked,
                self._last_cum_ack,
                self._last_cum_ack_time,
                False,
                False,
            )

        prev_cum_ack = self._last_cum_ack
        prev_cum_ack_time = self._last_cum_ack_time
        ack_advanced = False
        if self._last_cum_ack is None or seq_gt(ack, self._last_cum_ack):
            self._last_cum_ack = ack
            self._last_cum_ack_time = now
            ack_advanced = True
        self.update_sack_progress(ack, sack, ack_advanced)

        unacked_before = len(self._unacked)
        rtt_samples, acked_count, data_acked_count = self.process_ack(
            ack, sack, now=now, sample_rtt=sample_rtt
        )
        unacked_after = len(self._unacked)
        ack_progressed = acked_count > 0
        if ack_progressed:
            self._last_ack_progress_time = now

        return (
            rtt_samples,
            acked_count,
            data_acked_count,
            unacked_before,
            unacked_after,
            prev_cum_ack,
            prev_cum_ack_time,
            ack_advanced,
            ack_progressed,
        )

    def process_ack(self, ack, sack, now=None, sample_rtt=True):
        """
        Process incoming ACK and SACK.

        Returns:
            tuple: (rtt_samples, acked_count, data_acked_count)
                rtt_samples: list of RTT samples in ms for first-TX packets
                acked_count: count of newly acked packets (all acks)
                data_acked_count: count of newly acked packets with segments
        """
        self._require_now(now, 'process_ack')
        if self._ack_is_future(ack):
            return ([], 0, 0)

        rtt_samples = []
        acked_count = 0
        data_acked_count = 0
        acked_delta, data_acked_delta = self._ack_cumulative(
            ack, now, rtt_samples, sample_rtt
        )
        acked_count += acked_delta
        data_acked_count += data_acked_delta
        acked_delta, data_acked_delta = self._ack_sack(
            ack, sack, now, rtt_samples, sample_rtt
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

    def sack_progress_ready(self):
        cum_ack = self._last_cum_ack
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
        self._require_now(now, 'get_retransmits')
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

        items = []
        for _, seq, pkt in retransmits:
            items.append((seq, pkt.segments, pkt.flags, pkt.encrypted_body))
        return items

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

    def get_oldest_unacked_first_info(self):
        """
        Get oldest unacked packet by initial send time (Bob, opportunity-driven).

        Returns:
            tuple: (seq, segments, flags, encrypted_body, send_time,
                retransmit_count, first_send_time) or None
        """
        oldest = None
        oldest_time = None
        for seq, pkt in self._unacked.items():
            pkt_time = pkt.first_send_time
            if oldest is None or pkt_time < oldest_time:
                oldest = (seq, pkt)
                oldest_time = pkt_time
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
            pkt.first_send_time,
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
        pkt = self._unacked.get(seq)
        if pkt is None:
            return

        if now is None:
            raise SendWindowError(
                'Send window missing send_time',
                seq=seq,
                context='mark_retransmit',
            )
        pkt.retransmit_count += 1
        pkt.send_time = now
        if self._unacked_heap_enabled:
            self._push_unacked_heap(pkt)
        self._retransmit_count += 1
        self._stats.on_retransmit()

    def drop_keepalive(self, seq, reason=None, now=None):
        """
        Drop an unacked keepalive-only packet.
        """
        self._require_now(now, 'drop_keepalive')
        pkt = self._unacked.get(seq)
        if pkt is None:
            return False
        if not (pkt.flags & FLAG_KEEPALIVE):
            return False
        count_before = len(self._unacked)
        del self._unacked[seq]
        if pkt.segments:
            self._data_unacked_count -= 1
        count_after = len(self._unacked)
        self._record_keepalive_drop(
            seq, reason, now, count_before, count_after
        )
        return True

    def drop_oldest_keepalive(self, reason=None, now=None):
        """
        Drop the oldest unacked keepalive-only packet.
        """
        self._require_now(now, 'drop_oldest_keepalive')
        for seq, pkt in self._unacked.items():
            if pkt.flags & FLAG_KEEPALIVE:
                count_before = len(self._unacked)
                del self._unacked[seq]
                if pkt.segments:
                    self._data_unacked_count -= 1
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
        seqs = []
        for _, seq in candidates:
            seqs.append(seq)
        return seqs

    def get_keepalive_drop_info(self, now=None):
        self._require_now(now, 'get_keepalive_drop_info')
        if self._last_keepalive_drop_seq is None:
            return None
        age = None
        if self._last_keepalive_drop_time is not None:
            age = now - self._last_keepalive_drop_time
            age = round(age, 6)
        return {
            'keepalive_drop_seq': self._last_keepalive_drop_seq,
            'keepalive_drop_reason': self._last_keepalive_drop_reason,
            'keepalive_drop_age': age,
            'keepalive_drop_unacked_before': self._last_keepalive_drop_unacked_before,
            'keepalive_drop_unacked_after': self._last_keepalive_drop_unacked_after,
            'keepalive_drop_count': self._keepalive_drop_count,
        }

    def distance_info(self, cap_override=None, max_window=None):
        """
        Return distance info for send-window checks.

        Returns:
            tuple: (distance, max_in_flight, effective_cap, unacked,
            distance_limit, last_cum_ack, next_seq) or None.
        """
        if self._last_cum_ack is None:
            return None
        max_in_flight = self._max_in_flight
        effective_cap = max_in_flight
        if cap_override is not None and cap_override < effective_cap:
            effective_cap = cap_override
        if effective_cap < 1:
            effective_cap = 1
        next_seq = self._next_seq
        diff = seq_diff(next_seq, self._last_cum_ack)
        if diff < 0:
            return None
        distance = diff
        unacked = len(self._unacked)
        distance_limit = effective_cap
        if max_window is None:
            max_window = MAX_IN_FLIGHT
        if distance_limit > max_window:
            distance_limit = max_window
        return (
            distance,
            max_in_flight,
            effective_cap,
            unacked,
            distance_limit,
            self._last_cum_ack,
            next_seq,
        )

    def sack_hole_state(self, now=None):
        """
        Return minimal SACK hole state or None if unavailable.

        Returns:
            dict: last_cum_ack, missing_in_unacked, missing_age, distance,
                unacked, buffered
        """
        self._require_now(now, 'sack_hole_state')
        info = self.distance_info()
        if info is None:
            return None
        distance = info[0]
        unacked = info[3]
        last_cum_ack = info[5]
        if distance < unacked:
            distance = unacked
        buffered = distance - unacked
        missing_in_unacked = False
        missing_age = None
        missing_info = self.get_unacked_info(last_cum_ack)
        if missing_info is not None:
            missing_in_unacked = True
            send_time = missing_info[4]
            missing_age = now - send_time
        return {
            'last_cum_ack': last_cum_ack,
            'missing_in_unacked': missing_in_unacked,
            'missing_age': missing_age,
            'distance': distance,
            'unacked': unacked,
            'buffered': buffered,
        }

    def distance_exceeded(self, cap_override=None, max_window=None):
        """
        Check if next_seq is too far ahead of peer's cumulative ACK.

        Returns:
            tuple: (exceeded, fields) where fields is a tuple or None.
        """
        info = self.distance_info(
            cap_override=cap_override,
            max_window=max_window,
        )
        if info is None:
            return (False, None)
        distance = info[0]
        distance_limit = info[4]
        if distance < distance_limit:
            return (False, None)
        return (True, info)

    def distance_details(self, now=None):
        """
        Build debug fields to explain send-window distance stalls.
        """
        self._require_now(now, 'distance_details')
        last_cum_ack = self._last_cum_ack
        details = {
            'missing_seq': last_cum_ack,
            'missing_in_unacked': False,
            'missing_age': None,
            'missing_retransmit_count': None,
            'missing_flags': None,
            'missing_seg_count': None,
            'oldest_unacked_seq': None,
            'oldest_unacked_age': None,
            'oldest_unacked_retransmit_count': None,
            'oldest_unacked_flags': None,
            'oldest_unacked_seg_count': None,
        }
        missing_info = self.get_unacked_info(last_cum_ack)
        if missing_info is not None:
            (_, segments, flags, _,
             send_time, retransmit_count) = missing_info
            details['missing_in_unacked'] = True
            details['missing_retransmit_count'] = retransmit_count
            details['missing_flags'] = flags
            details['missing_seg_count'] = (
                len(segments) if segments is not None else 0
            )
            age = now - send_time
            details['missing_age'] = round(age, 6)
        oldest_info = self.get_oldest_unacked_info()
        if oldest_info is not None:
            (seq, segments, flags, _,
             send_time, retransmit_count) = oldest_info
            details['oldest_unacked_seq'] = seq
            details['oldest_unacked_retransmit_count'] = retransmit_count
            details['oldest_unacked_flags'] = flags
            details['oldest_unacked_seg_count'] = (
                len(segments) if segments is not None else 0
            )
            age = now - send_time
            details['oldest_unacked_age'] = round(age, 6)
        ack_info = self.get_ack_debug_info(
            seq=last_cum_ack, now=now
        )
        if ack_info is not None:
            details.update(ack_info)
        drop_info = self.get_keepalive_drop_info(now=now)
        if drop_info is not None:
            details.update(drop_info)
            details['missing_matches_keepalive_drop'] = (
                drop_info['keepalive_drop_seq'] == last_cum_ack
            )
        return details

    def debug_state(self, now=None):
        """
        Return a snapshot of send-window state for logging.
        """
        self._require_now(now, 'debug_state')
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
            age = now - send_time
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

    def _ack_cumulative(self, ack, now, rtt_samples, sample_rtt):
        acked_count = 0
        data_acked_count = 0
        while self._unacked:
            seq = next(iter(self._unacked))
            if not seq_lt(seq, ack):
                break
            acked_delta, data_acked_delta = self._ack_seq(
                seq,
                now,
                rtt_samples,
                is_sack=False,
                ack=ack,
                sack=None,
                sample_rtt=sample_rtt,
            )
            acked_count += acked_delta
            data_acked_count += data_acked_delta
        return (acked_count, data_acked_count)

    def _ack_sack(self, ack, sack, now, rtt_samples, sample_rtt):
        if sack == 0:
            return (0, 0)
        acked_count = 0
        data_acked_count = 0
        for offset in range(1, SACK_BITS + 1):
            if sack & (1 << (offset - 1)):
                seq = (ack + offset) & SEQ_MAX
                acked_delta, data_acked_delta = self._ack_seq(
                    seq,
                    now,
                    rtt_samples,
                    is_sack=True,
                    ack=ack,
                    sack=sack,
                    sample_rtt=sample_rtt,
                )
                acked_count += acked_delta
                data_acked_count += data_acked_delta
        return (acked_count, data_acked_count)

    def _ack_seq(self, seq, now, rtt_samples, is_sack, ack=None, sack=None,
                 sample_rtt=True):
        pkt = self._unacked.pop(seq, None)
        if pkt is None:
            self._record_ack_miss(seq, is_sack, now, ack, sack)
            return (0, 0)
        if pkt.segments:
            self._data_unacked_count -= 1
        self._record_ack_event(seq, pkt, now, is_sack, ack, sack)
        self._stats.on_ack(is_sack)
        if pkt.retransmit_count == 0:
            self._stats.on_ack_first_tx()
            if sample_rtt and not (pkt.flags & FLAG_KEEPALIVE):
                rtt_ms = (now - pkt.send_time) * 1000
                rtt_samples.append(rtt_ms)
                self._stats.on_rtt_sample()
        data_acked = 1 if pkt.segments else 0
        return (1, data_acked)

    def _select_oldest_unacked(self):
        if not self._unacked:
            return None
        if not self._unacked_heap_enabled:
            self._rebuild_unacked_heap()
            self._unacked_heap_enabled = True
        oldest = self._select_oldest_from_heap()
        if oldest is not None:
            return oldest
        return self._select_oldest_from_scan()

    def _select_oldest_from_heap(self):
        while self._unacked_heap:
            _send_time, token, seq = self._unacked_heap[0]
            pkt = self._unacked.get(seq)
            if pkt is None or pkt.heap_token != token:
                heapq.heappop(self._unacked_heap)
                continue
            return (seq, pkt)
        return None

    def _select_oldest_from_scan(self):
        oldest = None
        oldest_time = None
        for seq, pkt in self._unacked.items():
            pkt_time = pkt.send_time
            if oldest is None or pkt_time < oldest_time:
                oldest = (seq, pkt)
                oldest_time = pkt_time
        if oldest is None:
            return None
        self._rebuild_unacked_heap()
        return oldest

    def _rebuild_unacked_heap(self):
        self._unacked_heap = []
        for pkt in self._unacked.values():
            self._push_unacked_heap(pkt)

    def _push_unacked_heap(self, pkt):
        self._unacked_heap_token += 1
        token = self._unacked_heap_token
        pkt.heap_token = token
        pkt_time = pkt.send_time
        heapq.heappush(self._unacked_heap, (pkt_time, token, pkt.seq))

    def _ack_is_future(self, ack):
        return seq_gt(ack, self._next_seq)

    def get_ack_debug_info(self, seq=None, now=None):
        self._require_now(now, 'get_ack_debug_info')
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
        'first_send_time',
        'retransmit_count',
        'heap_token',
    )

    def __init__(self, seq, segments, flags, encrypted_body, send_time,
                 first_send_time, retransmit_count):
        self.seq = seq
        self.segments = segments
        self.flags = flags
        self.encrypted_body = encrypted_body
        self.send_time = send_time
        self.first_send_time = first_send_time
        self.retransmit_count = retransmit_count
        self.heap_token = None
