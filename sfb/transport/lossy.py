# -*- coding: ascii -*-
"""
Lossy transport wrappers for testing under poor network conditions.

Wraps any Transport or Server to inject configurable network impairment:
- Packet loss (random and burst)
- Delay and jitter
- Duplication
- Reordering
- Corruption
"""

from __future__ import absolute_import

import heapq
import random

from .transport_base import Transport, Server, TransportError, PendingTracker
from ..compat import to_bytes
from .. import time_provider


class NetworkImpairment(object):
    """
    Configurable network impairment parameters.

    All rates are probabilities from 0.0 to 1.0.
    """

    def __init__(self,
                 loss_rate=0.0,
                 burst_loss_prob=0.0,
                 burst_loss_len=(2, 8),
                 delay_ms=0,
                 jitter_ms=0,
                 dup_rate=0.0,
                 reorder_rate=0.0,
                 reorder_wait_ms=50,
                 corrupt_rate=0.0,
                 corrupt_bytes=(1, 3),
                 seed=None):
        """
        Configure network impairment.

        Args:
            loss_rate: Probability of dropping a packet (0.0-1.0)
            burst_loss_prob: Probability of entering burst loss mode
            burst_loss_len: (min, max) packets to drop in a burst
            delay_ms: Base delay in milliseconds
            jitter_ms: Random jitter +/- milliseconds
            dup_rate: Probability of duplicating a packet
            reorder_rate: Probability of holding packet for reordering
            reorder_wait_ms: How long to hold reordered packets
            corrupt_rate: Probability of corrupting packet bytes (mutate only)
            corrupt_bytes: (min, max) bytes to corrupt per packet
            seed: Random seed for reproducibility
        """
        self.loss_rate = loss_rate
        self.burst_loss_prob = burst_loss_prob
        self.burst_loss_len = burst_loss_len
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self.dup_rate = dup_rate
        self.reorder_rate = reorder_rate
        self.reorder_wait_ms = reorder_wait_ms
        self.corrupt_rate = corrupt_rate
        self.corrupt_bytes = corrupt_bytes
        self.seed = seed


class _ImpairmentDecision(object):
    __slots__ = ('drop', 'corrupt', 'delay_sec', 'duplicate_count', 'reorder')

    def __init__(self, drop, corrupt, delay_sec, duplicate_count, reorder):
        self.drop = drop
        self.corrupt = corrupt
        self.delay_sec = delay_sec
        self.duplicate_count = duplicate_count
        self.reorder = reorder


class _ImpairmentEngine(object):
    def __init__(self, config, stats_enabled=False):
        self._config = config
        self._stats_enabled = bool(stats_enabled)
        self._rng = random.Random(config.seed)
        self._burst_remaining = 0

        self._packets_sent = 0
        self._packets_dropped = 0
        self._packets_delayed = 0
        self._packets_duplicated = 0
        self._packets_reordered = 0
        self._packets_corrupted = 0

    def decide(self):
        if self._stats_enabled:
            self._packets_sent += 1

        drop = self._should_drop()
        if drop:
            return _ImpairmentDecision(True, False, 0.0, 0, False)

        corrupt = self._roll(self._config.corrupt_rate)
        if corrupt:
            if self._stats_enabled:
                self._packets_corrupted += 1

        delay_sec = self._calc_delay_sec()
        reorder = self._roll(self._config.reorder_rate)
        if reorder:
            if self._stats_enabled:
                self._packets_reordered += 1
            delay_sec += self._config.reorder_wait_ms / 1000.0

        duplicate_count = 1 if self._roll(self._config.dup_rate) else 0
        if duplicate_count:
            if self._stats_enabled:
                self._packets_duplicated += 1

        return _ImpairmentDecision(drop, corrupt, delay_sec, duplicate_count, reorder)

    def corrupt_bytes(self, data):
        data = to_bytes(data)
        length = len(data)
        if length == 0:
            return data
        min_bytes, max_bytes = self._config.corrupt_bytes
        if min_bytes < 0:
            min_bytes = 0
        if max_bytes < min_bytes:
            max_bytes = min_bytes
        if max_bytes <= 0:
            return data
        count = self._rng.randint(min_bytes, max_bytes)
        if count <= 0:
            return data
        count = min(count, length)
        mutated = bytearray(data)
        for _ in range(count):
            idx = self._rng.randint(0, length - 1)
            mask = self._rng.randint(1, 255)
            mutated[idx] ^= mask
        return bytes(mutated)

    def reset_stats(self):
        self._packets_sent = 0
        self._packets_dropped = 0
        self._packets_delayed = 0
        self._packets_duplicated = 0
        self._packets_reordered = 0
        self._packets_corrupted = 0

    def stats(self):
        if not self._stats_enabled:
            return {}
        return {
            'sent': self._packets_sent,
            'dropped': self._packets_dropped,
            'delayed': self._packets_delayed,
            'duplicated': self._packets_duplicated,
            'reordered': self._packets_reordered,
            'corrupted': self._packets_corrupted,
        }

    def _roll(self, rate):
        return rate > 0 and self._rng.random() < rate

    def _should_drop(self):
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            if self._stats_enabled:
                self._packets_dropped += 1
            return True

        if self._config.burst_loss_prob > 0:
            if self._rng.random() < self._config.burst_loss_prob:
                burst_len = self._rng.randint(
                    self._config.burst_loss_len[0],
                    self._config.burst_loss_len[1],
                )
                self._burst_remaining = max(0, burst_len - 1)
                if self._stats_enabled:
                    self._packets_dropped += 1
                return True

        if self._config.loss_rate > 0 and self._rng.random() < self._config.loss_rate:
            if self._stats_enabled:
                self._packets_dropped += 1
            return True

        return False

    def _calc_delay_sec(self):
        delay_ms = self._config.delay_ms
        jitter_ms = self._config.jitter_ms
        if delay_ms == 0 and jitter_ms == 0:
            return 0.0
        delay = delay_ms
        if jitter_ms > 0:
            delay += self._rng.uniform(-jitter_ms, jitter_ms)
        if delay > 0:
            if self._stats_enabled:
                self._packets_delayed += 1
        return max(0.0, delay / 1000.0)


class _EventQueue(object):
    __slots__ = ('_heap', '_seq')

    def __init__(self):
        self._heap = []
        self._seq = 0

    def push(self, deliver_at, item):
        heapq.heappush(self._heap, (deliver_at, self._seq, item))
        self._seq += 1

    def pop_ready(self, now):
        if not self._heap:
            return None
        if self._heap[0][0] > now:
            return None
        return heapq.heappop(self._heap)[2]

    def next_time(self):
        if not self._heap:
            return None
        return self._heap[0][0]

    def clear(self):
        self._heap = []

    def __len__(self):
        return len(self._heap)


class _ScheduledSend(object):
    __slots__ = ('wrapper_id', 'data', 'inner_permit', 'canceled')

    def __init__(self, wrapper_id, data, inner_permit):
        self.wrapper_id = wrapper_id
        self.data = data
        self.inner_permit = inner_permit
        self.canceled = False


class _ScheduledRecv(object):
    __slots__ = ('wrapper_id', 'data', 'requires_pending')

    def __init__(self, wrapper_id, data, requires_pending):
        self.wrapper_id = wrapper_id
        self.data = data
        self.requires_pending = requires_pending


class _PendingEntry(object):
    __slots__ = ('inner_ids', 'ghost_count', 'send_events')

    def __init__(self):
        self.inner_ids = set()
        self.ghost_count = 0
        self.send_events = []


class LossyTransport(Transport):
    """
    Wraps any Transport to inject network impairment.

    Impairment is applied bidirectionally:
    - send_impairment: affects outgoing packets (Alice -> Bob)
    - recv_impairment: affects incoming packets (Bob -> Alice)

    If only one impairment is provided, it's used for both directions.
    """

    def __init__(self, transport, send_impairment=None, recv_impairment=None,
                 pending_timeout_sec=5.0, stats_enabled=False):
        """
        Wrap a transport with network impairment.

        Args:
            transport: The inner Transport to wrap
            send_impairment: NetworkImpairment for outgoing packets
            recv_impairment: NetworkImpairment for incoming packets
                            (defaults to send_impairment if not provided)
            pending_timeout_sec: Timeout for synthetic drops and stale requests
            stats_enabled: Enable impairment stats counters
        """
        super(LossyTransport, self).__init__()
        self._inner = transport
        self._send_imp = send_impairment or NetworkImpairment()
        self._recv_imp = recv_impairment or self._send_imp

        self._stats_enabled = bool(stats_enabled)
        self._send_engine = _ImpairmentEngine(
            self._send_imp,
            stats_enabled=self._stats_enabled,
        )
        if self._recv_imp is self._send_imp:
            self._recv_engine = self._send_engine
        else:
            self._recv_engine = _ImpairmentEngine(
                self._recv_imp,
                stats_enabled=self._stats_enabled,
            )

        self._pending_timeout = pending_timeout_sec
        self._pending = PendingTracker(self._pending_timeout)
        self._inner_to_wrapper = {}
        self._next_corr_id = 0

        self._send_queue = _EventQueue()
        self._recv_queue = _EventQueue()

    @property
    def send_packet_mtu(self):
        return self._inner.send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._inner.recv_packet_mtu

    @property
    def max_in_flight(self):
        return getattr(self._inner, 'max_in_flight', None)

    def pending_count(self):
        return self._pending_total()

    def payload_cap_for_send(self, permit):
        if permit is None or not permit.data:
            return None
        inner_permit = permit.data.get('inner_permit')
        if inner_permit is None:
            return None
        return self._inner.payload_cap_for_send(inner_permit)

    def notify_send_pending(self, has_data):
        self._inner.notify_send_pending(has_data)

    def notify_peer_data(self, has_data):
        self._inner.notify_peer_data(has_data)

    def notify_recv_window_sack(self, sack):
        self._inner.notify_recv_window_sack(sack)

    def reserve_send(self, now=None):
        if now is None:
            now = time_provider.now()
        self._prune_pending(now)
        self._flush_send_queue(now)

        cap = self.max_in_flight
        pending_total = self._pending_total()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if cap is not None and pending_total + reserved >= cap:
            return None

        inner_permit = self._inner.reserve_send(now=now)
        if inner_permit is None:
            return None
        permit = self._reserve_permit(now=now, pending_before=pending_total)
        permit.data = {'inner_permit': inner_permit}
        return permit

    def _send_impl(self, data, permit):
        data = to_bytes(data)
        if len(data) > self.send_packet_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (
                    len(data), self.send_packet_mtu
                )
            )

        now = permit.now
        wrapper_id = self._next_corr_id
        self._next_corr_id = (self._next_corr_id + 1) & 0x7FFFFFFF

        entry = _PendingEntry()
        entry.ghost_count = 1
        self._pending.add(wrapper_id, entry, now=now)

        decision = self._send_engine.decide()
        if decision.drop:
            self._release_inner_permit(permit)
            return wrapper_id

        if decision.corrupt:
            data = self._send_engine.corrupt_bytes(data)

        inner_permit = permit.data.get('inner_permit')
        if inner_permit is None:
            raise TransportError('Missing inner send permit')

        delay_sec = decision.delay_sec
        if delay_sec > 0:
            self._schedule_send(wrapper_id, entry, data, inner_permit, now + delay_sec)
        else:
            self._send_now(wrapper_id, entry, data, inner_permit, consume_ghost=True)

        if decision.duplicate_count:
            dup_permit = self._inner.reserve_send(now=now)
            if dup_permit is not None:
                if delay_sec > 0:
                    entry.ghost_count += 1
                    self._schedule_send(
                        wrapper_id, entry, data, dup_permit, now + delay_sec
                    )
                else:
                    self._send_now(
                        wrapper_id, entry, data, dup_permit, consume_ghost=False
                    )
        return wrapper_id

    def _send_now(self, wrapper_id, entry, data, inner_permit, consume_ghost):
        inner_corr_id = self._inner.send(data, inner_permit)
        if consume_ghost and entry.ghost_count > 0:
            entry.ghost_count -= 1
        entry.inner_ids.add(inner_corr_id)
        self._inner_to_wrapper[inner_corr_id] = wrapper_id

    def _schedule_send(self, wrapper_id, entry, data, inner_permit, deliver_at):
        event = _ScheduledSend(wrapper_id, data, inner_permit)
        entry.send_events.append(event)
        self._send_queue.push(deliver_at, event)

    def release_send(self, permit):
        self._ensure_reserved()
        if permit is None:
            raise TransportError('Send permit required')
        if permit.transport is not self:
            raise TransportError('Send permit transport mismatch')
        if permit.used:
            raise TransportError('Send permit already used')
        if permit not in self._reserved:
            raise TransportError('Send permit not reserved')
        self._reserved.remove(permit)
        inner_permit = None
        if permit.data:
            inner_permit = permit.data.get('inner_permit')
        if inner_permit is not None:
            self._inner.release_send(inner_permit)

    def recv(self, timeout=None):
        now = time_provider.now()
        deadline = None if timeout is None else now + timeout

        while True:
            now = time_provider.now()
            self._prune_pending(now)
            self._flush_send_queue(now)

            ready = self._pop_ready_response(now)
            if ready is not None:
                return ready

            if timeout == 0:
                inner_timeout = 0
            else:
                remaining = None if deadline is None else deadline - now
                if remaining is not None and remaining <= 0:
                    return (None, None)
                next_due = self._next_due_time()
                if next_due is None:
                    inner_timeout = remaining
                else:
                    wait = max(0.0, next_due - now)
                    if remaining is None:
                        inner_timeout = wait
                    else:
                        inner_timeout = min(remaining, wait)

            corr_id, data = self._inner.recv(inner_timeout)
            if corr_id is None:
                if timeout == 0:
                    return (None, None)
                continue

            result = self._handle_inner_response(corr_id, data, time_provider.now())
            if result is not None:
                return result

    def _handle_inner_response(self, inner_corr_id, data, now):
        wrapper_id = self._inner_to_wrapper.pop(inner_corr_id, None)
        if wrapper_id is None:
            return None
        entry = self._pending.get(wrapper_id)
        if entry is None:
            return None
        if inner_corr_id in entry.inner_ids:
            entry.inner_ids.remove(inner_corr_id)

        decision = self._recv_engine.decide()
        if decision.drop:
            entry.ghost_count += 1
            return None

        if decision.corrupt:
            data = self._recv_engine.corrupt_bytes(data)

        delay_sec = decision.delay_sec
        if delay_sec > 0:
            entry.ghost_count += 1
            deliver_at = now + delay_sec
            self._recv_queue.push(
                deliver_at, _ScheduledRecv(wrapper_id, data, True)
            )
            if decision.duplicate_count:
                self._recv_queue.push(
                    deliver_at, _ScheduledRecv(wrapper_id, data, False)
                )
            return None

        result = (wrapper_id, data)
        if decision.duplicate_count:
            self._recv_queue.push(
                now, _ScheduledRecv(wrapper_id, data, False)
            )

        if entry.ghost_count + len(entry.inner_ids) == 0:
            self._pending.pop(wrapper_id)
        return result

    def _pop_ready_response(self, now):
        while True:
            event = self._recv_queue.pop_ready(now)
            if event is None:
                return None
            if event.requires_pending:
                entry = self._pending.get(event.wrapper_id)
                if entry is None:
                    continue
                if entry.ghost_count > 0:
                    entry.ghost_count -= 1
                if entry.ghost_count + len(entry.inner_ids) == 0:
                    self._pending.pop(event.wrapper_id)
            return (event.wrapper_id, event.data)

    def _pending_total(self):
        total = 0
        for entry, _ in self._pending._entries.values():
            total += entry.ghost_count + len(entry.inner_ids)
        return total

    def _next_due_time(self):
        next_send = self._send_queue.next_time()
        next_recv = self._recv_queue.next_time()
        if next_send is None:
            return next_recv
        if next_recv is None:
            return next_send
        return min(next_send, next_recv)

    def _flush_send_queue(self, now):
        while True:
            event = self._send_queue.pop_ready(now)
            if event is None:
                return
            if event.canceled:
                self._release_scheduled_send(event)
                continue
            entry = self._pending.get(event.wrapper_id)
            if entry is None:
                self._release_scheduled_send(event)
                continue
            if event in entry.send_events:
                entry.send_events.remove(event)
            inner_permit = event.inner_permit
            event.inner_permit = None
            inner_corr_id = self._inner.send(event.data, inner_permit)
            if entry.ghost_count > 0:
                entry.ghost_count -= 1
            entry.inner_ids.add(inner_corr_id)
            self._inner_to_wrapper[inner_corr_id] = event.wrapper_id

    def _prune_pending(self, now):
        stale = self._pending.prune(now=now)
        for wrapper_id, entry in stale:
            for inner_id in entry.inner_ids:
                self._inner_to_wrapper.pop(inner_id, None)
            for event in entry.send_events:
                event.canceled = True
                self._release_scheduled_send(event)
        return stale

    def _release_scheduled_send(self, event):
        if event.inner_permit is None:
            return
        self._inner.release_send(event.inner_permit)
        event.inner_permit = None

    def _release_inner_permit(self, permit):
        inner_permit = None
        if permit is not None and permit.data:
            inner_permit = permit.data.get('inner_permit')
        if inner_permit is not None:
            self._inner.release_send(inner_permit)
            permit.data['inner_permit'] = None

    def close(self):
        for wrapper_id, entry in list(self._pending._entries.items()):
            for event in entry[0].send_events:
                event.canceled = True
                self._release_scheduled_send(event)
        self._send_queue.clear()
        self._recv_queue.clear()
        self._pending.clear()
        self._inner_to_wrapper.clear()
        self._inner.close()

    def stats(self):
        if not self._stats_enabled:
            return {}
        return {
            'send': self._send_engine.stats(),
            'recv': self._recv_engine.stats(),
        }


class _ScheduledRequest(object):
    __slots__ = ('data', 'responder')

    def __init__(self, data, responder):
        self.data = data
        self.responder = responder


class _ScheduledResponse(object):
    __slots__ = ('responder', 'data')

    def __init__(self, responder, data):
        self.responder = responder
        self.data = data


class LossyServer(Server):
    """
    Wraps any Server to inject network impairment.

    Impairment is applied bidirectionally:
    - recv_impairment: affects incoming requests (Alice -> Bob)
    - send_impairment: affects outgoing responses (Bob -> Alice)

    If only one impairment is provided, it's used for both directions.
    """

    def __init__(self, server, recv_impairment=None, send_impairment=None,
                 stats_enabled=False):
        """
        Wrap a server with network impairment.

        Args:
            server: The inner Server to wrap
            recv_impairment: NetworkImpairment for incoming requests
            send_impairment: NetworkImpairment for outgoing responses
                            (defaults to recv_impairment if not provided)
            stats_enabled: Enable impairment stats counters
        """
        self._inner = server
        self._recv_imp = recv_impairment or NetworkImpairment()
        self._send_imp = send_impairment or self._recv_imp

        self._stats_enabled = bool(stats_enabled)
        self._recv_engine = _ImpairmentEngine(
            self._recv_imp,
            stats_enabled=self._stats_enabled,
        )
        if self._send_imp is self._recv_imp:
            self._send_engine = self._recv_engine
        else:
            self._send_engine = _ImpairmentEngine(
                self._send_imp,
                stats_enabled=self._stats_enabled,
            )

        self._request_queue = _EventQueue()
        self._response_queue = _EventQueue()

    @property
    def send_packet_mtu(self):
        return self._inner.send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._inner.recv_packet_mtu

    def recv(self, timeout=None):
        now = time_provider.now()
        deadline = None if timeout is None else now + timeout

        while True:
            now = time_provider.now()
            self._flush_response_queue(now)

            ready = self._pop_ready_request(now)
            if ready is not None:
                return ready

            if timeout == 0:
                inner_timeout = 0
            else:
                remaining = None if deadline is None else deadline - now
                if remaining is not None and remaining <= 0:
                    return (None, None)
                next_due = self._next_due_time()
                if next_due is None:
                    inner_timeout = remaining
                else:
                    wait = max(0.0, next_due - now)
                    if remaining is None:
                        inner_timeout = wait
                    else:
                        inner_timeout = min(remaining, wait)

            result = self._inner.recv(inner_timeout)
            if result is None or result[0] is None:
                if timeout == 0:
                    return (None, None)
                continue

            data, responder = result
            decision = self._recv_engine.decide()

            if decision.drop:
                continue
            if decision.corrupt:
                data = self._recv_engine.corrupt_bytes(data)

            wrapped_responder = self._wrap_responder(responder)
            delay_sec = decision.delay_sec
            if delay_sec > 0:
                deliver_at = time_provider.now() + delay_sec
                self._request_queue.push(
                    deliver_at, _ScheduledRequest(data, wrapped_responder)
                )
                if decision.duplicate_count:
                    self._request_queue.push(
                        deliver_at, _ScheduledRequest(data, wrapped_responder)
                    )
                continue

            if decision.duplicate_count:
                self._request_queue.push(
                    now, _ScheduledRequest(data, wrapped_responder)
                )
            return (data, wrapped_responder)

    def _wrap_responder(self, responder):
        send_engine = self._send_engine
        send_imp = self._send_imp

        def impaired_responder(data):
            data = to_bytes(data)
            decision = send_engine.decide()

            if decision.drop:
                return
            if decision.corrupt:
                data_mut = send_engine.corrupt_bytes(data)
            else:
                data_mut = data

            delay_sec = decision.delay_sec
            if delay_sec > 0 or decision.duplicate_count:
                deliver_at = time_provider.now() + delay_sec
                self._response_queue.push(
                    deliver_at, _ScheduledResponse(responder, data_mut)
                )
                if decision.duplicate_count:
                    self._response_queue.push(
                        deliver_at, _ScheduledResponse(responder, data_mut)
                    )
                return

            responder(data_mut)

        return impaired_responder

    def _pop_ready_request(self, now):
        while True:
            event = self._request_queue.pop_ready(now)
            if event is None:
                return None
            return (event.data, event.responder)

    def _flush_response_queue(self, now):
        while True:
            event = self._response_queue.pop_ready(now)
            if event is None:
                return
            event.responder(event.data)

    def _next_due_time(self):
        next_req = self._request_queue.next_time()
        next_resp = self._response_queue.next_time()
        if next_req is None:
            return next_resp
        if next_resp is None:
            return next_req
        return min(next_req, next_resp)

    def close(self):
        self._request_queue.clear()
        self._response_queue.clear()
        self._inner.close()

    def stats(self):
        if not self._stats_enabled:
            return {}
        return {
            'recv': self._recv_engine.stats(),
            'send': self._send_engine.stats(),
        }


# Convenience presets for common scenarios
def no_impairment():
    """No network impairment (passthrough)."""
    return NetworkImpairment()


def high_latency(delay_ms=500, jitter_ms=100, seed=None):
    """Satellite-like high latency."""
    return NetworkImpairment(
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        seed=seed,
    )


def moderate_loss(loss_rate=0.15, delay_ms=50, jitter_ms=25, seed=None):
    """Moderate packet loss (poor WiFi)."""
    return NetworkImpairment(
        loss_rate=loss_rate,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        seed=seed,
    )


def heavy_loss(loss_rate=0.40, delay_ms=100, jitter_ms=50, seed=None):
    """Heavy packet loss."""
    return NetworkImpairment(
        loss_rate=loss_rate,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        dup_rate=0.05,
        seed=seed,
    )


def burst_loss(loss_rate=0.10, burst_prob=0.15, burst_len=(3, 10),
               delay_ms=50, jitter_ms=25, seed=None):
    """Burst packet loss (interference)."""
    return NetworkImpairment(
        loss_rate=loss_rate,
        burst_loss_prob=burst_prob,
        burst_loss_len=burst_len,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        seed=seed,
    )


def extreme_conditions(loss_rate=0.50, delay_ms=300, jitter_ms=150,
                       dup_rate=0.10, reorder_rate=0.15, seed=None):
    """Extreme network conditions (near-unusable)."""
    return NetworkImpairment(
        loss_rate=loss_rate,
        burst_loss_prob=0.10,
        burst_loss_len=(2, 6),
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        dup_rate=dup_rate,
        reorder_rate=reorder_rate,
        reorder_wait_ms=100,
        seed=seed,
    )


def chaos(seed=None):
    """Everything bad happens."""
    return NetworkImpairment(
        loss_rate=0.30,
        burst_loss_prob=0.10,
        burst_loss_len=(2, 8),
        delay_ms=200,
        jitter_ms=150,
        dup_rate=0.15,
        reorder_rate=0.20,
        reorder_wait_ms=75,
        corrupt_rate=0.05,
        corrupt_bytes=(1, 4),
        seed=seed,
    )
