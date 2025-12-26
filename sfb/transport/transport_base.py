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
