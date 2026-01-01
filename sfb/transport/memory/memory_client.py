# -*- coding: ascii -*-
"""In-memory client transport (Alice)."""

from __future__ import absolute_import

import logging

from .memory_link import _InMemoryLink
from ..transport_base import Transport, TransportError
from ...compat import queue, to_bytes
from ...config import Config
from ...protocol.constants import DEFAULT_MAX_PACKET_SIZE
from ...logging_util import get_logger, log_event

_LOG = get_logger(__name__)


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
            self._send_mtu, self._recv_mtu, self._max_pending, config,
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
        data = to_bytes(data)
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
