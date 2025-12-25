# -*- coding: ascii -*-
"""
Abstract base classes for transports.

Transports handle the underlying I/O for the tunnel protocol. Due to the
asymmetric nature of covert channels (Alice polls, Bob responds), there
are two distinct transport interfaces:

    ClientTransport: Used by Alice. Sends data and waits for response.
    ServerTransport: Used by Bob. Waits for polls and sends responses.

Each exchange carries data in both directions - Alice's data goes in the
request, Bob's data comes back in the response.
"""

from __future__ import absolute_import

import abc


class ClientTransport(object):
    """
    Abstract base for client-side (Alice) transports.

    Alice initiates exchanges. Each exchange sends her data and returns
    Bob's response data.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def exchange(self, data):
        """
        Send data and wait for response.

        Args:
            data: bytes to send

        Returns:
            bytes: response data

        Raises:
            TransportError: on I/O failure
        """
        pass

    @abc.abstractproperty
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one exchange.

        Returns:
            int: max send size
        """
        pass

    @abc.abstractproperty
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one exchange.

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


class ServerTransport(object):
    """
    Abstract base for server-side (Bob) transports.

    Bob waits for Alice's polls. Each poll delivers Alice's data and
    provides a way to send Bob's response.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def recv(self, timeout=None):
        """
        Wait for a poll from Alice.

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

    @abc.abstractproperty
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one poll.

        Returns:
            int: max receive size
        """
        pass

    @abc.abstractproperty
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
