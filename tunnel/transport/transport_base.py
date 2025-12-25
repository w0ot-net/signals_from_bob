# -*- coding: ascii -*-
"""
Abstract base classes for transports.

Transports handle the underlying I/O for the tunnel protocol. Due to the
asymmetric nature of covert channels (Alice polls, Bob responds), there
are two distinct transport families:

    RequestResponseTransport: For polling/request-response mediums
    StreamTransport: For bidirectional streams or datagrams

Each exchange carries data in both directions - Alice's data goes in the
request, Bob's data comes back in the response.
"""

from __future__ import absolute_import

import abc


class RequestResponseTransport(object):
    """
    Abstract base for request-response transports.

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

    @property
    @abc.abstractmethod
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one exchange.

        Returns:
            int: max send size
        """
        pass

    @property
    @abc.abstractmethod
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


class RequestResponseServer(object):
    """
    Abstract base for server-side request-response transports.

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

    @property
    @abc.abstractmethod
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one poll.

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


class StreamTransport(object):
    """
    Abstract base for bidirectional stream transports (TCP).
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def send(self, data):
        """
        Send raw bytes.

        Args:
            data: bytes to send
        """
        pass

    @abc.abstractmethod
    def recv(self, size, timeout=None):
        """
        Receive up to size bytes.

        Args:
            size: max bytes to read
            timeout: max seconds to wait (None = block forever)

        Returns:
            bytes: received data (may be shorter than size)
        """
        pass

    @property
    @abc.abstractmethod
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one call.

        Returns:
            int: max send size
        """
        pass

    @property
    @abc.abstractmethod
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one call.

        Returns:
            int: max receive size
        """
        pass

    def close(self):
        """Close the transport and release resources."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class DatagramTransport(object):
    """
    Abstract base for bidirectional datagram transports (UDP).
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def sendto(self, data, addr):
        """
        Send a datagram.

        Args:
            data: bytes to send
            addr: destination address tuple
        """
        pass

    @abc.abstractmethod
    def recvfrom(self, timeout=None):
        """
        Receive a datagram.

        Args:
            timeout: max seconds to wait (None = block forever)

        Returns:
            tuple: (data, addr)
        """
        pass

    @property
    @abc.abstractmethod
    def send_mtu(self):
        """
        Maximum bytes that can be sent in one datagram.

        Returns:
            int: max send size
        """
        pass

    @property
    @abc.abstractmethod
    def recv_mtu(self):
        """
        Maximum bytes that can be received in one datagram.

        Returns:
            int: max receive size
        """
        pass

    def close(self):
        """Close the transport and release resources."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class TransportError(Exception):
    """Base exception for transport errors."""
    pass
