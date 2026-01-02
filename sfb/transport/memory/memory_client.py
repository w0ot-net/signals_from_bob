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

    def __init__(self, config, link=None, send_mtu=None, recv_mtu=None):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(InMemoryTransport, self).__init__()
        self._send_mtu = send_mtu or DEFAULT_MAX_PACKET_SIZE
        self._recv_mtu = recv_mtu or DEFAULT_MAX_PACKET_SIZE
        self._max_in_flight = getattr(config, 'max_in_flight', 128)
        self._link = link or _InMemoryLink(
            self._send_mtu, self._recv_mtu, config,
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
    def max_in_flight(self):
        return self._max_in_flight

    def pending_count(self):
        return len(self._pending)

    def reserve_send(self, now=None):
        if self._link.is_closed():
            raise TransportError('In-memory transport closed')
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        pending_total = pending_before + reserved
        if pending_total >= self._max_in_flight:
            log_event(
                _LOG,
                logging.DEBUG,
                'memory.send_blocked',
                'In-memory transport send blocked',
                lambda: {
                    'pending': pending_before,
                    'reserved': reserved,
                    'pending_total': pending_total,
                    'max_in_flight': self._max_in_flight,
                },
            )
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def _send_impl(self, data, permit):
        if self._link.is_closed():
            raise TransportError('In-memory transport closed')
        data = to_bytes(data)
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )

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
