# -*- coding: ascii -*-
"""
UDP ephemeral server transport for Bob.
"""

from __future__ import absolute_import

import logging
import select
import socket

from ..transport_base import Server, TransportError, raise_bind_error
from ..mtu_limits import resolve_mtu_limits
from .udp_ephemeral_config import validate_udp_ephemeral_config
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)


class UdpEphemeralServer(Server):
    """
    UDP ephemeral server transport for Bob.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')

        validated = validate_udp_ephemeral_config(config, role='server')
        self._config = config
        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'udp_ephemeral', config, role='server'
        )
        self._recv_packet_mtu = recv_mtu
        self._send_packet_mtu = send_mtu
        self._listen_addr = validated['listen_addr']
        self._recv_bufsize = max(1, self._recv_packet_mtu + 1)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind(self._listen_addr)
            self._sock.setblocking(False)
        except (socket.error, OSError) as exc:
            self._sock.close()
            self._sock = None
            raise_bind_error(exc, self._listen_addr, 'UDP ephemeral')

        mtu_details = {
            'transport': 'udp_ephemeral',
            'role': 'server',
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'min_packet_mtu': min_packet_mtu,
        }
        mtu_details.update(mtu_constraints)
        log_event(
            _LOG,
            logging.INFO,
            'transport.mtu_limits',
            'Transport MTU limits',
            lambda: mtu_details,
        )
        log_event(
            _LOG,
            logging.INFO,
            'udp_ephemeral.server_config',
            'UDP ephemeral server config',
            lambda: {
                'listen_addr': '%s:%d' % (
                    self._listen_addr[0], self._listen_addr[1]
                ),
                'recv_packet_mtu': self._recv_packet_mtu,
                'send_packet_mtu': self._send_packet_mtu,
            },
        )

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    def recv(self, timeout=None):
        deadline = None
        use_deadline = False
        if timeout is not None and timeout > 0:
            deadline = time_provider.now() + timeout
            use_deadline = True
        while True:
            try:
                if timeout is None:
                    wait = None
                elif timeout == 0:
                    wait = 0
                elif use_deadline:
                    remaining = deadline - time_provider.now()
                    if remaining <= 0:
                        return (None, None)
                    wait = remaining
                else:
                    wait = timeout
                ready, _, _ = select.select([self._sock], [], [], wait)
                if not ready:
                    return (None, None)
                data, addr = self._sock.recvfrom(self._recv_bufsize)
            except select.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'udp_ephemeral.select_failed',
                    'UDP ephemeral select failed',
                    lambda: {'error': str(e)},
                )
                raise TransportError('Select failed: %s' % e)
            except socket.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'udp_ephemeral.recv_failed',
                    'UDP ephemeral receive failed',
                    lambda: {'error': str(e)},
                )
                raise TransportError('Receive failed: %s' % e)

            if len(data) > self._recv_packet_mtu:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'udp_ephemeral.oversize_request',
                    'UDP ephemeral request oversized',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(data),
                        'recv_packet_mtu': self._recv_packet_mtu,
                    },
                )
                continue

            responder = self._make_responder(addr)
            log_event(
                _LOG,
                logging.DEBUG,
                'udp_ephemeral.recv',
                'UDP ephemeral request received',
                lambda: {
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': len(data),
                },
            )
            return (data, responder)

    def _make_responder(self, addr):
        def responder(data):
            data = require_bytes_like(data)
            if len(data) > self._send_packet_mtu:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'udp_ephemeral.send_oversize',
                    'UDP ephemeral response oversized',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(data),
                        'send_packet_mtu': self._send_packet_mtu,
                    },
                )
                raise TransportError(
                    'Data size %d exceeds send MTU %d' %
                    (len(data), self._send_packet_mtu)
                )
            try:
                self._sock.sendto(data, addr)
            except socket.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'udp_ephemeral.send_failed',
                    'UDP ephemeral response send failed',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(data),
                        'error': str(e),
                    },
                )
                raise TransportError('Send failed: %s' % e)
            log_event(
                _LOG,
                logging.DEBUG,
                'udp_ephemeral.send',
                'UDP ephemeral response sent',
                lambda: {
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': len(data),
                },
            )
        return responder

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None
