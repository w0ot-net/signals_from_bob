# -*- coding: ascii -*-
"""
ICMP server transport for Bob.
"""

from __future__ import absolute_import

import logging
import os
import select
import socket

from ..transport_base import Server, TransportError
from .icmp_packet import ICMP_ECHO_REQUEST, build_echo_reply, parse_icmp_echo
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)


class IcmpServer(Server):
    """
    ICMP server transport for Bob.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        self._config = config
        self._require_privileges()
        self._require_kernel_echo_disabled()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_RAW,
                                   socket.IPPROTO_ICMP)
        self._sock.setblocking(False)

        self._recv_mtu = config.icmp_payload_mtu
        self._send_mtu = config.icmp_payload_mtu
        self._recv_bufsize = 65535
        log_event(
            _LOG,
            logging.INFO,
            'icmp.server_config',
            'ICMP server config',
            lambda: {
                'recv_mtu': self._recv_mtu,
                'send_mtu': self._send_mtu,
            },
        )

    @property
    def recv_mtu(self):
        return self._recv_mtu

    @property
    def send_mtu(self):
        return self._send_mtu

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
                packet, addr = self._sock.recvfrom(self._recv_bufsize)
            except select.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'icmp.select_failed',
                    'ICMP select failed',
                    lambda: {'error': str(e)},
                )
                raise TransportError('Select failed: %s' % e)
            except socket.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'icmp.recv_failed',
                    'ICMP receive failed',
                    lambda: {'error': str(e)},
                )
                raise TransportError('Receive failed: %s' % e)

            result, reason = parse_icmp_echo(
                packet,
                expect_type=ICMP_ECHO_REQUEST,
                validate_checksum=False,
            )
            if result is None:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'icmp.malformed_request',
                    'ICMP request malformed',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(packet),
                        'reason': reason,
                    },
                )
                continue

            _, ident, seq, payload = result
            if len(payload) > self._recv_mtu:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'icmp.oversize_request',
                    'ICMP request oversized',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(payload),
                        'recv_mtu': self._recv_mtu,
                        'corr_id': seq,
                    },
                )
                continue

            responder = self._make_responder(addr, ident, seq)
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.recv',
                'ICMP echo request received',
                lambda: {
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': len(payload),
                    'corr_id': seq,
                },
            )
            return (payload, responder)

    def _make_responder(self, addr, ident, seq):
        def responder(data):
            data = require_bytes_like(data)
            if len(data) > self._send_mtu:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'icmp.send_oversize',
                    'ICMP response oversized',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(data),
                        'send_mtu': self._send_mtu,
                        'corr_id': seq,
                    },
                )
                raise TransportError(
                    'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
                )
            packet = build_echo_reply(ident, seq, data)
            try:
                self._sock.sendto(packet, addr)
            except socket.error as e:
                log_event(
                    _LOG,
                    logging.WARNING,
                    'icmp.send_failed',
                    'ICMP echo reply send failed',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(packet),
                        'payload_bytes': len(data),
                        'corr_id': seq,
                        'error': str(e),
                    },
                )
                raise TransportError('Send failed: %s' % e)
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.send',
                'ICMP echo reply sent',
                lambda: {
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': len(packet),
                    'payload_bytes': len(data),
                    'corr_id': seq,
                },
            )
        return responder

    def _require_privileges(self):
        if os.name != 'posix':
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.unsupported_os',
                'ICMP transport requires Linux raw sockets',
                lambda: {'os': os.name},
            )
            raise TransportError('ICMP transport requires Linux raw sockets')
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.privileges_required',
                'ICMP transport requires root privileges',
                lambda: None,
            )
            raise TransportError('ICMP transport requires root privileges')

    def _require_kernel_echo_disabled(self):
        path = '/proc/sys/net/ipv4/icmp_echo_ignore_all'
        try:
            handle = open(path, 'r')
        except (IOError, OSError):
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.kernel_echo_check_failed',
                'Unable to read kernel ICMP echo setting',
                lambda: {'path': path},
            )
            raise TransportError('Unable to read %s' % path)
        with handle:
            value = handle.read().strip()
        if value == '0':
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.kernel_echo_enabled',
                'Kernel ICMP echo replies are enabled',
                lambda: {'path': path},
            )
            raise TransportError(
                'Kernel ICMP echo replies are enabled.\n'
                'Disable them with:\n'
                '  sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1'
            )

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None
