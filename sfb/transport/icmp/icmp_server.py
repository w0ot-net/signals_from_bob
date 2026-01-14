# -*- coding: ascii -*-
"""
ICMP server transport for Bob.
"""

from __future__ import absolute_import

import errno
import logging
import os
import select
import socket

from ..transport_base import Server, TransportError
from ..mtu_limits import resolve_mtu_limits
from .icmp_packet import ICMP_ECHO_REQUEST, build_echo_reply, parse_icmp_echo
from ...compat import PY2, buffer_view, require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)

_WOULD_BLOCK = set([errno.EAGAIN, errno.EWOULDBLOCK])
for name in ('WSAEWOULDBLOCK',):
    value = getattr(errno, name, None)
    if value is not None:
        _WOULD_BLOCK.add(value)


def _get_errno(exc):
    err = getattr(exc, 'errno', None)
    if err is None and getattr(exc, 'args', None):
        if exc.args:
            try:
                err = int(exc.args[0])
            except (TypeError, ValueError):
                err = None
    return err


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
        self._sock_list = [self._sock]

        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'icmp', config, role='server'
        )
        self._recv_packet_mtu = recv_mtu
        self._send_packet_mtu = send_mtu
        self._recv_bufsize = 65535
        self._recv_buffer = None
        self._recvfrom_into = None
        if not PY2 and hasattr(self._sock, 'recvfrom_into'):
            self._recv_buffer = bytearray(self._recv_bufsize)
            self._recvfrom_into = self._sock.recvfrom_into
        mtu_details = {
            'transport': 'icmp',
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
        if _LOG.isEnabledFor(logging.INFO):
            log_event(
                _LOG,
                logging.INFO,
                'icmp.server_config',
                'ICMP server config',
                lambda: {
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
                ready, _, _ = select.select(self._sock_list, [], [], wait)
                if not ready:
                    return (None, None)
            except select.error as e:
                if _LOG.isEnabledFor(logging.WARNING):
                    log_event(
                        _LOG,
                        logging.WARNING,
                        'icmp.select_failed',
                        'ICMP select failed',
                        lambda: {'error': str(e)},
                    )
                raise TransportError('Select failed: %s' % e)

            result = self._try_recv_drain()
            if result[0] is not None:
                return result
            if timeout == 0:
                return (None, None)

    def _try_recv_drain(self):
        while True:
            packet, addr = self._recv_packet()
            if packet is None:
                return (None, None)
            result = self._parse_request(packet, addr)
            if result[0] is not None:
                return result

    def _recv_packet(self):
        if self._recvfrom_into is not None and self._recv_buffer is not None:
            if len(self._recv_buffer) != self._recv_bufsize:
                self._recv_buffer = bytearray(self._recv_bufsize)
            try:
                recv_len, addr = self._recvfrom_into(self._recv_buffer)
            except socket.error as e:
                err = _get_errno(e)
                if err in _WOULD_BLOCK:
                    return (None, None)
                if _LOG.isEnabledFor(logging.WARNING):
                    log_event(
                        _LOG,
                        logging.WARNING,
                        'icmp.recv_failed',
                        'ICMP receive failed',
                        lambda: {'error': str(e)},
                    )
                raise TransportError('Receive failed: %s' % e)
            packet = buffer_view(self._recv_buffer, length=recv_len)
            return (packet, addr)
        try:
            packet, addr = self._sock.recvfrom(self._recv_bufsize)
        except socket.error as e:
            err = _get_errno(e)
            if err in _WOULD_BLOCK:
                return (None, None)
            if _LOG.isEnabledFor(logging.WARNING):
                log_event(
                    _LOG,
                    logging.WARNING,
                    'icmp.recv_failed',
                    'ICMP receive failed',
                    lambda: {'error': str(e)},
                )
            raise TransportError('Receive failed: %s' % e)
        return (packet, addr)

    def _parse_request(self, packet, addr):
        result, reason = parse_icmp_echo(
            packet,
            expect_type=ICMP_ECHO_REQUEST,
            validate_checksum=False,
        )
        if result is None:
            if _LOG.isEnabledFor(logging.DEBUG):
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
            return (None, None)

        _, ident, seq, payload = result
        if len(payload) > self._recv_packet_mtu:
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'icmp.oversize_request',
                    'ICMP request oversized',
                    lambda: {
                        'addr': '%s:%d' % (addr[0], addr[1]),
                        'bytes': len(payload),
                        'recv_packet_mtu': self._recv_packet_mtu,
                        'corr_id': seq,
                    },
                )
            return (None, None)

        responder = self._make_responder(addr, ident, seq)
        if _LOG.isEnabledFor(logging.DEBUG):
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
            if len(data) > self._send_packet_mtu:
                if _LOG.isEnabledFor(logging.DEBUG):
                    log_event(
                        _LOG,
                        logging.DEBUG,
                        'icmp.send_oversize',
                        'ICMP response oversized',
                        lambda: {
                            'addr': '%s:%d' % (addr[0], addr[1]),
                            'bytes': len(data),
                            'send_packet_mtu': self._send_packet_mtu,
                            'corr_id': seq,
                        },
                    )
                raise TransportError(
                    'Data size %d exceeds send MTU %d' % (
                        len(data), self._send_packet_mtu
                    )
                )
            packet = build_echo_reply(ident, seq, data)
            try:
                self._sock.sendto(packet, addr)
            except socket.error as e:
                if _LOG.isEnabledFor(logging.WARNING):
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
            if _LOG.isEnabledFor(logging.DEBUG):
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
            if _LOG.isEnabledFor(logging.WARNING):
                log_event(
                    _LOG,
                    logging.WARNING,
                    'icmp.unsupported_os',
                    'ICMP transport requires Linux raw sockets',
                    lambda: {'os': os.name},
                )
            raise TransportError('ICMP transport requires Linux raw sockets')
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            if _LOG.isEnabledFor(logging.WARNING):
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
            if _LOG.isEnabledFor(logging.WARNING):
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
            if _LOG.isEnabledFor(logging.WARNING):
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
            self._sock_list = None
