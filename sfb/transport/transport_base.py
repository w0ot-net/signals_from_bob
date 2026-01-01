# -*- coding: ascii -*-
"""
Abstract base classes for transports.

Transports handle the underlying I/O for the tunnel protocol. Due to the
asymmetric nature of covert channels (Alice polls, Bob responds), transports
use a request/response pattern at the wire level.

The Transport interface separates send() and recv() to support pipelining -
multiple requests in flight simultaneously. For serial operation, set
max_pending=1 or call recv() after each send().

    Transport: Client side (Alice) - send requests, receive responses
    Server: Server side (Bob) - receive requests, send responses
"""

from __future__ import absolute_import

import abc
import time


class Transport(object):
    """
    Abstract base for client transports with pipelining support.

    Alice uses send() to dispatch requests and recv() to collect responses.
    Correlation IDs returned by send() are used to match responses.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def send(self, data):
        """
        Send data to Bob.

        Args:
            data: bytes to send

        Returns:
            int: Correlation ID for matching response

        Raises:
            TransportError: on I/O failure
        """
        pass

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

        Returns:
            int: count of pending requests
        """
        pass

    @property
    @abc.abstractmethod
    def max_pending(self):
        """
        Maximum concurrent in-flight requests.

        Returns:
            int: max pending requests (transport limit)
        """
        pass

    @property
    @abc.abstractmethod
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one request.

        Returns:
            int: max send size
        """
        pass

    @property
    @abc.abstractmethod
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one response.

        Returns:
            int: max receive size
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

            Returns (None, None) on timeout.

        Raises:
            TransportError: on I/O failure
        """
        pass

    @property
    @abc.abstractmethod
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one request.

        Returns:
            int: max receive size
        """
        pass

    @property
    @abc.abstractmethod
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one response.

        Returns:
            int: max send size
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
        self._last_refill = time.time()

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
            now = time.time()
        self._refill(now)
        return self._tokens >= amount

    def take(self, amount=1.0, now=None):
        if self._rate <= 0:
            return True
        if now is None:
            now = time.time()
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
        now = time.time()
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
            now = time.time()
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
            now = time.time()
        stale = []
        for key, (value, ts) in list(self._entries.items()):
            if now - ts > self._timeout:
                stale.append((key, value))
                del self._entries[key]
        return stale

    def __len__(self):
        return len(self._entries)
