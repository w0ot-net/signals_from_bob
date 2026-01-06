# -*- coding: ascii -*-
"""
Abstract base classes for transports.

Transports handle the underlying I/O for the tunnel protocol. Due to the
asymmetric nature of covert channels (Alice polls, Bob responds), transports
use a request/response pattern at the wire level.

The Transport interface separates reserve_send(), send(), and recv() to
support pipelining - multiple requests in flight simultaneously. For serial
operation, set max_in_flight=1 or call recv() after each send().

    Transport: Client side (Alice) - send requests, receive responses
    Server: Server side (Bob) - receive requests, send responses
"""

from __future__ import absolute_import

import abc
import errno

from .. import time_provider


def with_metaclass(meta, *bases):
    class TemporaryClass(object):
        pass
    return meta('TemporaryClass', bases, {})


class TransportMeta(abc.ABCMeta):
    def __new__(mcls, name, bases, namespace):
        if name != 'Transport' and 'send' in namespace:
            raise TypeError('Transport subclasses must not override send()')
        return super(TransportMeta, mcls).__new__(mcls, name, bases, namespace)


class SendPermit(object):
    __slots__ = ('transport', 'now', 'pending_before', 'used', 'data')

    def __init__(self, transport, now, pending_before=None, data=None):
        self.transport = transport
        self.now = now
        self.pending_before = pending_before
        self.used = False
        self.data = data


class Transport(with_metaclass(TransportMeta, object)):
    """
    Abstract base for client transports with pipelining support.

    Alice reserves via reserve_send(), uses send() to dispatch requests, and
    uses recv() to collect responses.
    Correlation IDs returned by send() are used to match responses.
    """

    def __init__(self):
        self._reserved = set()

    @abc.abstractmethod
    def reserve_send(self, now=None):
        """
        Reserve capacity for a send attempt.

        Args:
            now: optional timestamp to reuse

        Returns:
            SendPermit or None if capacity is exhausted

        Raises:
            TransportError: on I/O failure
        """
        pass

    @abc.abstractmethod
    def _send_impl(self, data, permit):
        """
        Transport-specific send implementation.

        Args:
            data: bytes to send
            permit: SendPermit reserved by this transport

        Returns:
            int: Correlation ID for matching response

        Raises:
            TransportError: on I/O failure
        """
        pass

    def send(self, data, permit):
        """
        Send data to Bob using a reserved permit.

        Args:
            data: bytes to send
            permit: SendPermit returned by reserve_send()

        Returns:
            int: Correlation ID for matching response

        Raises:
            TransportError: on I/O failure or invalid permit
        """
        self._ensure_reserved()
        if permit is None:
            raise TransportError('Send permit required')
        if permit.transport is not self:
            raise TransportError('Send permit transport mismatch')
        if permit.used:
            raise TransportError('Send permit already used')
        if permit not in self._reserved:
            raise TransportError('Send permit not reserved')
        permit.used = True
        try:
            return self._send_impl(data, permit)
        finally:
            self._reserved.discard(permit)

    def release_send(self, permit):
        """
        Release a reserved permit when a send is skipped.

        Args:
            permit: SendPermit returned by reserve_send()

        Raises:
            TransportError: on invalid permit
        """
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

    @abc.abstractmethod
    def recv(self, timeout=None):
        """
        Receive next available response.

        Args:
            timeout: Max seconds to wait
                     None = block until response
                     0 = non-blocking poll

        Returns:
            tuple: (correlation_id, data) on success
                   (None, None) on timeout

        Raises:
            TransportError: on I/O failure
        """
        pass

    @abc.abstractmethod
    def pending_count(self):
        """
        Number of requests awaiting response.

        This is a non-pruning count; pruning occurs in reserve_send() and recv().

        Returns:
            int: count of pending requests
        """
        pass

    @property
    @abc.abstractmethod
    def max_in_flight(self):
        """
        Maximum concurrent in-flight requests.

        Returns:
            int: max in-flight requests (transport limit)
        """
        pass

    @property
    @abc.abstractmethod
    def send_packet_mtu(self):
        """
        Maximum bytes that can be sent in one request.

        Returns:
            int: max send size in packet bytes
        """
        pass

    @property
    @abc.abstractmethod
    def recv_packet_mtu(self):
        """
        Maximum bytes that can be received in one response.

        Returns:
            int: max receive size in packet bytes
        """
        pass

    def payload_cap_for_send(self, permit):
        """
        Optional per-send packet cap for tunnel payload collection.

        Args:
            permit: SendPermit returned by reserve_send()

        Returns:
            int: packet byte cap or None
        """
        return None

    def notify_send_pending(self, has_data):
        """
        Optional hint about Alice's pending data state.

        Args:
            has_data: bool indicating queued non-control data for this send
                      attempt
        """
        return None

    def notify_peer_data(self, has_data):
        """
        Optional hint about peer data state.

        Args:
            has_data: bool indicating peer sent non-control data
        """
        return None

    def notify_recv_window_sack(self, sack):
        """
        Optional hint about Alice's receive window SACK state.

        Args:
            sack: int SACK bitmap from Alice's recv window
        """
        return None

    def close(self):
        """
        Close the transport and release resources.

        Default implementation does nothing. Subclasses should override
        if they hold resources.
        """
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _ensure_reserved(self):
        if not hasattr(self, '_reserved') or self._reserved is None:
            self._reserved = set()

    def _reserve_permit(self, now=None, pending_before=None, data=None):
        self._ensure_reserved()
        if now is None:
            now = time_provider.now()
        permit = SendPermit(
            transport=self,
            now=now,
            pending_before=pending_before,
            data=data,
        )
        self._reserved.add(permit)
        return permit


class Server(object):
    """
    Abstract base for server-side transports.

    Bob waits for Alice's requests. Each request delivers Alice's data and
    provides a responder callable to send Bob's response.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def recv(self, timeout=None):
        """
        Wait for a request from Alice.

        Args:
            timeout: max seconds to wait (None = block forever)

        Returns:
            tuple: (data, responder) where:
                - data: bytes received from Alice
                - responder: callable that takes bytes and sends response
                  Optional attribute: responder.response_payload_cap (packet bytes)

            Returns (None, None) on timeout.

        Raises:
            TransportError: on I/O failure
        """
        pass

    @property
    @abc.abstractmethod
    def recv_packet_mtu(self):
        """
        Maximum bytes that can be received in one request.

        Returns:
            int: max receive size in packet bytes
        """
        pass

    @property
    @abc.abstractmethod
    def send_packet_mtu(self):
        """
        Maximum bytes that can be sent in one response.

        Returns:
            int: max send size in packet bytes
        """
        pass

    def close(self):
        """
        Close the transport and release resources.

        Default implementation does nothing. Subclasses should override
        if they hold resources.
        """
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class TransportError(Exception):
    """Base exception for transport errors."""
    pass


_ADDR_IN_USE_ERRORS = set([errno.EADDRINUSE])
for name in ('WSAEADDRINUSE',):
    value = getattr(errno, name, None)
    if value is not None:
        _ADDR_IN_USE_ERRORS.add(value)

_PERM_ERRORS = set([errno.EACCES])
for name in ('WSAEACCES',):
    value = getattr(errno, name, None)
    if value is not None:
        _PERM_ERRORS.add(value)


def _get_errno(exc):
    err = getattr(exc, 'errno', None)
    if err is None and getattr(exc, 'args', None):
        if exc.args:
            try:
                err = int(exc.args[0])
            except (TypeError, ValueError):
                err = None
    return err


def _format_listen_addr(listen_addr):
    if isinstance(listen_addr, (tuple, list)) and len(listen_addr) == 2:
        return '%s:%s' % (listen_addr[0], listen_addr[1])
    return str(listen_addr)


def raise_bind_error(exc, listen_addr, transport_label):
    err = _get_errno(exc)
    listen_label = _format_listen_addr(listen_addr)
    if err in _ADDR_IN_USE_ERRORS:
        raise TransportError(
            '%s listen address already in use: %s' % (transport_label, listen_label)
        )
    if err in _PERM_ERRORS:
        raise TransportError(
            'Permission denied binding %s listen address: %s' % (
                transport_label, listen_label
            )
        )
    raise TransportError(
        'Failed to bind %s listen address %s: %s' % (
            transport_label, listen_label, exc
        )
    )


class TokenBucket(object):
    """
    Simple token bucket rate limiter.
    """

    def __init__(self, rate, capacity=None):
        self._rate = float(rate)
        if capacity is None:
            capacity = rate
        self._capacity = float(capacity)
        self._tokens = self._capacity
        self._last_refill = time_provider.now()

    def _refill(self, now):
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def can_take(self, amount=1.0, now=None):
        if self._rate <= 0:
            return True
        if now is None:
            now = time_provider.now()
        self._refill(now)
        return self._tokens >= amount

    def take(self, amount=1.0, now=None):
        if self._rate <= 0:
            return True
        if now is None:
            now = time_provider.now()
        self._refill(now)
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False


class RateLimiter(object):
    """
    Transport-agnostic rate limiter wrapper around TokenBucket.

    Used by higher layers (e.g., tunnel) to gate send pacing independently
    from transport-specific constraints.
    """

    def __init__(self, rate_per_sec, burst=None):
        self._enabled = rate_per_sec is not None and float(rate_per_sec) > 0
        if not self._enabled:
            self._bucket = None
            return
        capacity = burst if burst is not None else rate_per_sec
        self._bucket = TokenBucket(rate_per_sec, capacity=capacity)

    def can_send(self, amount=1.0, now=None):
        if not self._enabled:
            return True
        return self._bucket.can_take(amount, now=now)

    def consume(self, amount=1.0, now=None):
        if not self._enabled:
            return True
        return self._bucket.take(amount, now=now)


def prune_and_count(pending, prune_fn, now=None, on_prune=None):
    """
    Prune pending entries via prune_fn and return the post-prune count.

    Args:
        pending: PendingTracker instance
        prune_fn: callable that accepts now and returns stale entries
        now: optional timestamp to reuse
        on_prune: optional callback(stale) for extra cleanup

    Returns:
        int: count after pruning
    """
    if now is None:
        now = time_provider.now()
    stale = prune_fn(now=now)
    if stale is None:
        stale = []
    if on_prune is not None and stale:
        on_prune(stale)
    return len(pending)


class PendingTracker(object):
    """
    Tracks pending requests with timeouts.
    """

    def __init__(self, timeout):
        self._timeout = timeout
        self._entries = {}

    def add(self, key, value, now=None):
        if now is None:
            now = time_provider.now()
        self._entries[key] = (value, now)

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        return entry[0]

    def pop(self, key, default=None):
        entry = self._entries.pop(key, None)
        if entry is None:
            return default
        return entry[0]

    def clear(self):
        self._entries.clear()

    def prune(self, now=None):
        if now is None:
            now = time_provider.now()
        stale = []
        for key, (value, ts) in list(self._entries.items()):
            if now - ts > self._timeout:
                stale.append((key, value))
                del self._entries[key]
        return stale

    def __len__(self):
        return len(self._entries)
