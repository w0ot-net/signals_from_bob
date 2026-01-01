# -*- coding: ascii -*-
"""In-memory server transport (Bob)."""

from __future__ import absolute_import

import logging

from .memory_link import _InMemoryLink
from ..transport_base import Server, TransportError
from ...compat import require_bytes, queue
from ...config import Config
from ...protocol.constants import DEFAULT_MAX_PACKET_SIZE

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
            request_mtu, response_mtu, link_max_pending, config,
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
