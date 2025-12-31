# -*- coding: ascii -*-
"""
In-memory transport for local testing.

Provides a Transport/Server pair backed by in-process queues. Useful for
unit tests and simulations without any network I/O.
"""

from __future__ import absolute_import

import logging
import threading

try:
    import Queue as queue
except ImportError:
    import queue

from .transport_base import Transport, Server, TransportError
from ..compat import require_bytes
from ..config import Config
from ..protocol.constants import DEFAULT_MAX_PACKET_SIZE
from ..logging_util import get_logger, log_event

_LOG = get_logger(__name__)


class _InMemoryLink(object):
    """Shared state between in-memory client and server."""

    __slots__ = (
        'request_mtu', 'response_mtu', 'max_pending',
        '_requests', '_responses', '_closed', '_lock',
    )

    def __init__(self, request_mtu, response_mtu, max_pending):
        self.request_mtu = request_mtu
        self.response_mtu = response_mtu
        self.max_pending = max_pending
        self._requests = queue.Queue()
        self._responses = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._requests.put_nowait(None)
        except Exception:
            pass
        try:
            self._responses.put_nowait((None, None))
        except Exception:
            pass

    def is_closed(self):
        with self._lock:
            return self._closed


class InMemoryTransport(Transport):
    """Client-side in-memory transport (Alice)."""

    def __init__(self, config, link=None, send_mtu=None, recv_mtu=None,
                 max_pending=None):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        self._send_mtu = send_mtu or DEFAULT_MAX_PACKET_SIZE
        self._recv_mtu = recv_mtu or DEFAULT_MAX_PACKET_SIZE
        self._max_pending = (
            max_pending if max_pending is not None
            else getattr(config, 'tunnel_max_in_flight', 64)
        )
        self._link = link or _InMemoryLink(
            self._send_mtu, self._recv_mtu, self._max_pending
        )
        self._next_corr_id = 0
        self._pending = set()

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    @property
    def max_pending(self):
        return self._max_pending

    def pending_count(self):
        return len(self._pending)

    def send(self, data):
        if self._link.is_closed():
            raise TransportError('In-memory transport closed')
        data = require_bytes(data)
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )
        if self.pending_count() >= self._max_pending:
            log_event(
                _LOG,
                logging.DEBUG,
                'memory.send_blocked',
                'In-memory transport send blocked',
                {
                    'pending': self.pending_count(),
                    'max_pending': self._max_pending,
                },
            )
            raise TransportError('Too many pending in-memory requests')

        corr_id = self._next_corr_id
        self._next_corr_id = (self._next_corr_id + 1) & 0x7FFFFFFF
        self._pending.add(corr_id)
        self._link._requests.put((corr_id, data))
        return corr_id

    def recv(self, timeout=None):
        try:
            if timeout is None:
                item = self._link._responses.get(True)
            elif timeout == 0:
                item = self._link._responses.get(False)
            else:
                item = self._link._responses.get(True, timeout)
        except queue.Empty:
            return (None, None)

        if item is None or item == (None, None):
            return (None, None)

        corr_id, data = item
        self._pending.discard(corr_id)
        return (corr_id, data)

    def close(self):
        self._pending.clear()
        self._link.close()


class InMemoryServer(Server):
    """Server-side in-memory transport (Bob)."""

    def __init__(self, config, link=None, send_mtu=None, recv_mtu=None,
                 max_pending=None):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        request_mtu = recv_mtu or DEFAULT_MAX_PACKET_SIZE
        response_mtu = send_mtu or DEFAULT_MAX_PACKET_SIZE
        link_max_pending = (
            max_pending if max_pending is not None
            else getattr(config, 'tunnel_max_in_flight', 64)
        )
        self._link = link or _InMemoryLink(
            request_mtu, response_mtu, link_max_pending
        )
        self._send_mtu = self._link.response_mtu
        self._recv_mtu = self._link.request_mtu

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    def recv(self, timeout=None):
        try:
            if timeout is None:
                item = self._link._requests.get(True)
            elif timeout == 0:
                item = self._link._requests.get(False)
            else:
                item = self._link._requests.get(True, timeout)
        except queue.Empty:
            return (None, None)

        if item is None:
            return (None, None)

        corr_id, data = item

        def responder(response_data):
            if self._link.is_closed():
                return
            response_data = require_bytes(response_data)
            if len(response_data) > self._send_mtu:
                raise TransportError(
                    'Response size %d exceeds send MTU %d' % (
                        len(response_data), self._send_mtu
                    )
                )
            self._link._responses.put((corr_id, response_data))

        return (data, responder)

    def close(self):
        self._link.close()


def create_inmemory_transport_pair(config, send_mtu=None, recv_mtu=None,
                                   max_pending=None):
    """
    Create a connected in-memory Transport/Server pair.

    Args:
        config: Config instance
        send_mtu: Optional request MTU (Alice->Bob)
        recv_mtu: Optional response MTU (Bob->Alice)
        max_pending: Optional max in-flight requests

    Returns:
        tuple: (InMemoryTransport, InMemoryServer)
    """
    link = _InMemoryLink(
        send_mtu or DEFAULT_MAX_PACKET_SIZE,
        recv_mtu or DEFAULT_MAX_PACKET_SIZE,
        max_pending if max_pending is not None
        else getattr(config, 'tunnel_max_in_flight', 64),
    )
    return (
        InMemoryTransport(
            config, link=link, send_mtu=send_mtu, recv_mtu=recv_mtu,
            max_pending=max_pending,
        ),
        InMemoryServer(
            config, link=link, send_mtu=recv_mtu, recv_mtu=send_mtu,
            max_pending=max_pending,
        ),
    )
