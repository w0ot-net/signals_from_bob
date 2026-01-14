# -*- coding: ascii -*-
"""In-memory server transport (Bob)."""

from __future__ import absolute_import

import logging

from .memory_link import _InMemoryLink
from ..transport_base import Server, TransportError
from ..mtu_limits import resolve_mtu_limits
from ...compat import queue, to_bytes
from ...config import Config
from ...logging_util import get_logger, log_event

_LOG = get_logger(__name__)

class InMemoryServer(Server):
    """Server-side in-memory transport (Bob)."""

    def __init__(self, config, link=None, send_packet_mtu=None,
                 recv_packet_mtu=None):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'memory',
            config,
            role='server',
            send_packet_mtu=send_packet_mtu,
            recv_packet_mtu=recv_packet_mtu,
        )
        request_mtu = recv_mtu
        response_mtu = send_mtu
        self._link = link or _InMemoryLink(
            request_mtu, response_mtu, config,
        )
        self._send_packet_mtu = self._link.response_mtu
        self._recv_packet_mtu = self._link.request_mtu
        mtu_details = {
            'transport': 'memory',
            'role': 'server',
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'min_packet_mtu': min_packet_mtu,
        }
        mtu_details.update(mtu_constraints)
        if _LOG.isEnabledFor(logging.INFO):
            log_event(
                _LOG,
                logging.INFO,
                'transport.mtu_limits',
                'Transport MTU limits',
                lambda: mtu_details,
            )

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

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
            response_data = to_bytes(response_data)
            if len(response_data) > self._send_packet_mtu:
                raise TransportError(
                    'Response size %d exceeds send MTU %d' % (
                        len(response_data), self._send_packet_mtu
                    )
                )
            self._link._responses.put((corr_id, response_data))

        return (data, responder)

    def close(self):
        self._link.close()
