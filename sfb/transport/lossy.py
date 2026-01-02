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

import random
from collections import deque

from .transport_base import Transport, Server, TransportError
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
            corrupt_rate: Probability of corrupting packet bytes
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

        self._rng = random.Random(seed)
        self._burst_remaining = 0

        # Stats
        self.packets_sent = 0
        self.packets_dropped = 0
        self.packets_delayed = 0
        self.packets_duplicated = 0
        self.packets_reordered = 0
        self.packets_corrupted = 0

    def should_drop(self):
        """Check if packet should be dropped (loss or burst)."""
        # Burst loss takes priority
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            self.packets_dropped += 1
            return True

        # Check for burst start
        if self.burst_loss_prob > 0 and self._rng.random() < self.burst_loss_prob:
            burst_len = self._rng.randint(
                self.burst_loss_len[0], self.burst_loss_len[1]
            )
            self._burst_remaining = max(0, burst_len - 1)
            self.packets_dropped += 1
            return True

        # Random loss
        if self.loss_rate > 0 and self._rng.random() < self.loss_rate:
            self.packets_dropped += 1
            return True

        return False

    def should_duplicate(self):
        """Check if packet should be duplicated."""
        if self.dup_rate > 0 and self._rng.random() < self.dup_rate:
            self.packets_duplicated += 1
            return True
        return False

    def should_reorder(self):
        """Check if packet should be held for reordering."""
        if self.reorder_rate > 0 and self._rng.random() < self.reorder_rate:
            self.packets_reordered += 1
            return True
        return False

    def should_corrupt(self):
        """Check if packet should be corrupted."""
        if self.corrupt_rate > 0 and self._rng.random() < self.corrupt_rate:
            self.packets_corrupted += 1
            return True
        return False

    def get_delay_sec(self):
        """Get delay for this packet in seconds."""
        if self.delay_ms == 0 and self.jitter_ms == 0:
            return 0
        delay = self.delay_ms
        if self.jitter_ms > 0:
            delay += self._rng.uniform(-self.jitter_ms, self.jitter_ms)
        if delay > 0:
            self.packets_delayed += 1
        return max(0, delay / 1000.0)

    def get_reorder_delay_sec(self):
        """Get delay for reordered packet."""
        return self.reorder_wait_ms / 1000.0

    def reset_stats(self):
        """Reset statistics counters."""
        self.packets_sent = 0
        self.packets_dropped = 0
        self.packets_delayed = 0
        self.packets_duplicated = 0
        self.packets_reordered = 0
        self.packets_corrupted = 0

    def stats(self):
        """Return statistics dictionary."""
        return {
            'sent': self.packets_sent,
            'dropped': self.packets_dropped,
            'delayed': self.packets_delayed,
            'duplicated': self.packets_duplicated,
            'reordered': self.packets_reordered,
            'corrupted': self.packets_corrupted,
        }


class _DelayedPacket(object):
    """A packet waiting to be delivered after a delay."""

    __slots__ = ('corr_id', 'data', 'deliver_at')

    def __init__(self, corr_id, data, deliver_at):
        self.corr_id = corr_id
        self.data = data
        self.deliver_at = deliver_at


class LossyTransport(Transport):
    """
    Wraps any Transport to inject network impairment.

    Impairment is applied bidirectionally:
    - send_impairment: affects outgoing packets (Alice -> Bob)
    - recv_impairment: affects incoming packets (Bob -> Alice)

    If only one impairment is provided, it's used for both directions.
    """

    def __init__(self, transport, send_impairment=None, recv_impairment=None):
        """
        Wrap a transport with network impairment.

        Args:
            transport: The inner Transport to wrap
            send_impairment: NetworkImpairment for outgoing packets
            recv_impairment: NetworkImpairment for incoming packets
                            (defaults to send_impairment if not provided)
        """
        super(LossyTransport, self).__init__()
        self._inner = transport
        self._send_imp = send_impairment or NetworkImpairment()
        self._recv_imp = recv_impairment or self._send_imp

        # Track fake corr_ids for dropped packets with timestamps
        self._next_fake_id = 0x80000000
        self._dropped_ids = {}  # fake_id -> drop_time
        self._drop_timeout = 5.0  # Clean up after 5 seconds

        # Buffer for delayed/reordered responses
        self._delayed = deque()

    @property
    def send_mtu(self):
        return self._inner.send_mtu

    @property
    def recv_mtu(self):
        return self._inner.recv_mtu

    @property
    def max_in_flight(self):
        return getattr(self._inner, 'max_in_flight', None)

    def pending_count(self):
        # Include packets we "sent" but dropped
        return self._inner.pending_count() + len(self._dropped_ids)

    def reserve_send(self, now=None):
        if now is None:
            now = time_provider.now()

        inner_permit = self._inner.reserve_send(now=now)
        self._prune_dropped(now)

        cap = self.max_in_flight
        if cap is not None:
            pending_before = self.pending_count()
            self._ensure_reserved()
            reserved = len(self._reserved)
            pending_total = pending_before + reserved
            if pending_total >= cap:
                if inner_permit is not None:
                    self._inner.release_send(inner_permit)
                return None
        else:
            pending_before = self.pending_count()

        action = 'send'
        if self._send_imp.should_drop():
            action = 'drop'
        elif self._send_imp.should_corrupt():
            action = 'corrupt'

        dup_permit = None
        duplicate = False
        if action == 'send':
            if inner_permit is None:
                return None
            if self._send_imp.should_duplicate():
                if not self._send_imp.should_corrupt():
                    dup_permit = self._inner.reserve_send(now=now)
                    if dup_permit is not None:
                        duplicate = True
        else:
            if inner_permit is not None:
                self._inner.release_send(inner_permit)
                inner_permit = None

        permit = self._reserve_permit(now=now, pending_before=pending_before)
        permit.data = {
            'action': action,
            'inner_permit': inner_permit,
            'dup_permit': dup_permit if duplicate else None,
        }
        return permit

    def _send_impl(self, data, permit):
        """Send with possible impairment."""
        self._send_imp.packets_sent += 1
        info = permit.data or {}
        action = info.get('action', 'send')
        inner_permit = info.get('inner_permit')
        dup_permit = info.get('dup_permit')

        if action in ('drop', 'corrupt'):
            fake_id = self._next_fake_id
            self._next_fake_id += 1
            self._dropped_ids[fake_id] = permit.now
            return fake_id

        if inner_permit is None:
            raise TransportError('Missing inner send permit')

        try:
            corr_id = self._inner.send(data, inner_permit)
        except Exception:
            if dup_permit is not None:
                self._inner.release_send(dup_permit)
            raise
        if dup_permit is not None:
            self._inner.send(data, dup_permit)
        return corr_id

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
        info = permit.data or {}
        inner_permit = info.get('inner_permit')
        dup_permit = info.get('dup_permit')
        if inner_permit is not None:
            self._inner.release_send(inner_permit)
        if dup_permit is not None:
            self._inner.release_send(dup_permit)

    def recv(self, timeout=None):
        """Receive with possible impairment."""
        now = time_provider.now()

        # First, check delayed buffer for ready packets
        ready = self._check_delayed(now)
        if ready is not None:
            return ready

        # Clean up old dropped packet IDs
        self._prune_dropped(now)

        # Calculate adjusted timeout
        if timeout is None:
            inner_timeout = None
        elif timeout == 0:
            inner_timeout = 0
        else:
            # Check if we have delayed packets that will be ready soon
            next_ready = self._next_delayed_time()
            if next_ready is not None:
                wait_time = next_ready - now
                if wait_time <= 0:
                    inner_timeout = 0
                else:
                    inner_timeout = min(timeout, wait_time)
            else:
                inner_timeout = timeout

        # Try to receive from inner transport
        corr_id, data = self._inner.recv(inner_timeout)

        # Check delayed again after waiting
        if corr_id is None:
            now = time_provider.now()
            ready = self._check_delayed(now)
            if ready is not None:
                return ready
            return (None, None)

        # Apply recv impairment
        self._recv_imp.packets_sent += 1

        # Check for drop
        if self._recv_imp.should_drop():
            # Try to get another packet
            return self.recv(timeout=0)

        # Check for corruption (simulate lower-layer discard)
        if self._recv_imp.should_corrupt():
            return self.recv(timeout=0)

        # Check for delay/reorder
        delay = self._recv_imp.get_delay_sec()
        if self._recv_imp.should_reorder():
            delay += self._recv_imp.get_reorder_delay_sec()

        if delay > 0:
            deliver_at = now + delay
            self._delayed.append(_DelayedPacket(corr_id, data, deliver_at))
            # Try to return something else
            return self.recv(timeout=0)

        # Check for duplication (queue a delayed copy)
        if self._recv_imp.should_duplicate():
            dup_delay = self._recv_imp.get_delay_sec()
            if dup_delay == 0:
                dup_delay = 0.001  # Tiny delay to ensure ordering
            self._delayed.append(_DelayedPacket(corr_id, data, now + dup_delay))

        return (corr_id, data)

    def _check_delayed(self, now):
        """Check for delayed packets ready to deliver."""
        if not self._delayed:
            return None

        # Find earliest ready packet
        ready_idx = None
        for i, pkt in enumerate(self._delayed):
            if pkt.deliver_at <= now:
                if ready_idx is None or pkt.deliver_at < self._delayed[ready_idx].deliver_at:
                    ready_idx = i

        if ready_idx is not None:
            pkt = self._delayed[ready_idx]
            del self._delayed[ready_idx]
            return (pkt.corr_id, pkt.data)

        return None

    def _next_delayed_time(self):
        """Get delivery time of next delayed packet."""
        if not self._delayed:
            return None
        return min(pkt.deliver_at for pkt in self._delayed)

    def _prune_dropped(self, now):
        if not self._dropped_ids:
            return
        expired = [fid for fid, t in self._dropped_ids.items()
                   if now - t >= self._drop_timeout]
        for fid in expired:
            del self._dropped_ids[fid]

    def close(self):
        """Close the transport."""
        self._delayed.clear()
        self._dropped_ids.clear()
        self._inner.close()

    def stats(self):
        """Return impairment statistics."""
        return {
            'send': self._send_imp.stats(),
            'recv': self._recv_imp.stats(),
        }


class LossyServer(Server):
    """
    Wraps any Server to inject network impairment.

    Impairment is applied bidirectionally:
    - recv_impairment: affects incoming requests (Alice -> Bob)
    - send_impairment: affects outgoing responses (Bob -> Alice)

    If only one impairment is provided, it's used for both directions.
    """

    def __init__(self, server, recv_impairment=None, send_impairment=None):
        """
        Wrap a server with network impairment.

        Args:
            server: The inner Server to wrap
            recv_impairment: NetworkImpairment for incoming requests
            send_impairment: NetworkImpairment for outgoing responses
                            (defaults to recv_impairment if not provided)
        """
        self._inner = server
        self._recv_imp = recv_impairment or NetworkImpairment()
        self._send_imp = send_impairment or self._recv_imp

        # Buffer for delayed incoming requests
        self._delayed_requests = deque()

    @property
    def send_mtu(self):
        return self._inner.send_mtu

    @property
    def recv_mtu(self):
        return self._inner.recv_mtu

    def recv(self, timeout=None):
        """Receive request with possible impairment."""
        now = time_provider.now()

        # Check delayed requests first
        ready = self._check_delayed_requests(now)
        if ready is not None:
            return ready

        # Calculate adjusted timeout
        if timeout is None:
            inner_timeout = None
        elif timeout == 0:
            inner_timeout = 0
        else:
            next_ready = self._next_delayed_request_time()
            if next_ready is not None:
                wait_time = next_ready - now
                if wait_time <= 0:
                    inner_timeout = 0
                else:
                    inner_timeout = min(timeout, wait_time)
            else:
                inner_timeout = timeout

        # Get from inner server
        result = self._inner.recv(inner_timeout)
        if result is None or result[0] is None:
            # Check delayed again
            now = time_provider.now()
            ready = self._check_delayed_requests(now)
            if ready is not None:
                return ready
            return (None, None)

        data, responder = result
        self._recv_imp.packets_sent += 1

        # Check for drop
        if self._recv_imp.should_drop():
            # Silently drop - don't call responder
            # Try to get another request
            return self.recv(timeout=0)

        # Check for corruption (simulate lower-layer discard)
        if self._recv_imp.should_corrupt():
            return self.recv(timeout=0)

        # Check for delay/reorder
        delay = self._recv_imp.get_delay_sec()
        if self._recv_imp.should_reorder():
            delay += self._recv_imp.get_reorder_delay_sec()

        if delay > 0:
            deliver_at = now + delay
            wrapped_responder = self._wrap_responder(responder)
            self._delayed_requests.append(
                _DelayedRequest(data, wrapped_responder, deliver_at)
            )
            return self.recv(timeout=0)

        # Wrap responder to apply send impairment
        wrapped_responder = self._wrap_responder(responder)

        # Check for duplication
        if self._recv_imp.should_duplicate():
            dup_delay = self._recv_imp.get_delay_sec()
            if dup_delay == 0:
                dup_delay = 0.001
            self._delayed_requests.append(
                _DelayedRequest(data, wrapped_responder, now + dup_delay)
            )

        return (data, wrapped_responder)

    def _wrap_responder(self, responder):
        """Wrap responder to apply send impairment."""
        send_imp = self._send_imp

        def impaired_responder(data):
            send_imp.packets_sent += 1

            # Check for drop
            if send_imp.should_drop():
                return  # Silently drop response

            # Check for corruption (simulate lower-layer discard)
            if send_imp.should_corrupt():
                return  # Silently drop response

            # Send response
            responder(data)

            # Check for duplication
            if send_imp.should_duplicate():
                responder(data)

        return impaired_responder

    def _check_delayed_requests(self, now):
        """Check for delayed requests ready to deliver."""
        if not self._delayed_requests:
            return None

        ready_idx = None
        for i, req in enumerate(self._delayed_requests):
            if req.deliver_at <= now:
                if ready_idx is None or req.deliver_at < self._delayed_requests[ready_idx].deliver_at:
                    ready_idx = i

        if ready_idx is not None:
            req = self._delayed_requests[ready_idx]
            del self._delayed_requests[ready_idx]
            return (req.data, req.responder)

        return None

    def _next_delayed_request_time(self):
        """Get delivery time of next delayed request."""
        if not self._delayed_requests:
            return None
        return min(req.deliver_at for req in self._delayed_requests)

    def close(self):
        """Close the server."""
        self._delayed_requests.clear()
        self._inner.close()

    def stats(self):
        """Return impairment statistics."""
        return {
            'recv': self._recv_imp.stats(),
            'send': self._send_imp.stats(),
        }


class _DelayedRequest(object):
    """A request waiting to be delivered after a delay."""

    __slots__ = ('data', 'responder', 'deliver_at')

    def __init__(self, data, responder, deliver_at):
        self.data = data
        self.responder = responder
        self.deliver_at = deliver_at


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
