# -*- coding: ascii -*-
"""
ICMP client transport for Alice.
"""

from __future__ import absolute_import

import errno
import logging
import os
import random
import select
import socket

from ..transport_base import (
    Transport,
    TransportError,
    PendingTracker,
    prune_and_count,
)
from ..mtu_limits import resolve_mtu_limits
from .icmp_packet import ICMP_ECHO_REPLY, build_echo_request, parse_icmp_echo
from ...compat import PY2, buffer_view, require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)
_DRAIN_LIMIT = 32

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


def _normalize_socket_buffer(value):
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _set_socket_buffer(sock, opt, opt_name, value, role):
    try:
        sock.setsockopt(socket.SOL_SOCKET, opt, value)
        return True
    except socket.error as e:
        log_event(
            _LOG,
            logging.WARNING,
            'icmp.socket_buffer_set_failed',
            'ICMP socket buffer set failed',
            lambda: {
                'role': role,
                'buffer': opt_name,
                'value': value,
                'error': str(e),
            },
        )
        return False


def _get_socket_buffer(sock, opt, opt_name, role):
    try:
        return sock.getsockopt(socket.SOL_SOCKET, opt)
    except socket.error as e:
        log_event(
            _LOG,
            logging.WARNING,
            'icmp.socket_buffer_get_failed',
            'ICMP socket buffer get failed',
            lambda: {
                'role': role,
                'buffer': opt_name,
                'error': str(e),
            },
        )
        return None


def _configure_socket_buffers(sock, rcvbuf, sndbuf, role):
    rcvbuf = _normalize_socket_buffer(rcvbuf)
    sndbuf = _normalize_socket_buffer(sndbuf)
    if rcvbuf is None and sndbuf is None:
        return
    if rcvbuf is not None:
        _set_socket_buffer(sock, socket.SO_RCVBUF, 'rcvbuf', rcvbuf, role)
    if sndbuf is not None:
        _set_socket_buffer(sock, socket.SO_SNDBUF, 'sndbuf', sndbuf, role)
    effective_rcv = None
    effective_snd = None
    if rcvbuf is not None:
        effective_rcv = _get_socket_buffer(
            sock, socket.SO_RCVBUF, 'rcvbuf', role
        )
    if sndbuf is not None:
        effective_snd = _get_socket_buffer(
            sock, socket.SO_SNDBUF, 'sndbuf', role
        )
    log_event(
        _LOG,
        logging.INFO,
        'icmp.socket_buffers',
        'ICMP socket buffers',
        lambda: {
            'role': role,
            'rcvbuf_requested': rcvbuf,
            'sndbuf_requested': sndbuf,
            'rcvbuf_effective': effective_rcv,
            'sndbuf_effective': effective_snd,
        },
    )


class IcmpClient(Transport):
    """
    ICMP client transport for Alice.

    Sends ICMP Echo Requests carrying SFB packets and receives Echo Replies.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(IcmpClient, self).__init__()
        self._config = config
        self._require_privileges()

        if not config.icmp_target:
            raise TransportError('icmp_target required for ICMP transport')

        self._target_ip = self._resolve_target(config.icmp_target)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_RAW,
                                   socket.IPPROTO_ICMP)
        self._sock.setblocking(False)
        self._sock_list = [self._sock]
        self._target_addr = (self._target_ip, 0)
        _configure_socket_buffers(
            self._sock,
            config.icmp_socket_rcvbuf,
            config.icmp_socket_sndbuf,
            role='client',
        )

        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'icmp', config, role='client'
        )
        self._send_packet_mtu = send_mtu
        self._recv_packet_mtu = recv_mtu
        self._max_in_flight = config.max_in_flight
        self._pending_timeout = config.icmp_pending_timeout
        self._recv_bufsize = 65535
        self._recv_buffer = None
        self._recvfrom_into = None
        if not PY2 and hasattr(self._sock, 'recvfrom_into'):
            self._recv_buffer = bytearray(self._recv_bufsize)
            self._recvfrom_into = self._sock.recvfrom_into

        self._pending = PendingTracker(self._pending_timeout)
        self._next_seq = random.randint(0, 0xFFFF)
        self._icmp_id = random.randint(0, 0xFFFF)
        mtu_details = {
            'transport': 'icmp',
            'role': 'client',
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
            'icmp.client_config',
            'ICMP client config',
            lambda: {
                'target': config.icmp_target,
                'target_ip': self._target_ip,
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'socket_rcvbuf': config.icmp_socket_rcvbuf,
                'socket_sndbuf': config.icmp_socket_sndbuf,
            },
        )

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def max_in_flight(self):
        return self._max_in_flight

    def reserve_send(self, now=None):
        if now is None:
            now = time_provider.now()
        pending_before = prune_and_count(
            self._pending, self._prune_stale, now=now
        )
        self._ensure_reserved()
        reserved = len(self._reserved)
        pending_total = pending_before + reserved
        if pending_total >= self._max_in_flight:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.send_blocked',
                'ICMP send blocked',
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
        pending_before = permit.pending_before
        if pending_before is None:
            pending_before = len(self._pending)

        data = require_bytes_like(data)
        if len(data) > self._send_packet_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (
                    len(data), self._send_packet_mtu
                )
            )

        seq = self._next_sequence()
        packet = build_echo_request(self._icmp_id, seq, data)

        try:
            self._sock.sendto(packet, self._target_addr)
        except socket.error as e:
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.send_failed',
                'ICMP echo request send failed',
                lambda: {
                    'target': self._target_ip,
                    'bytes': len(packet),
                    'payload_bytes': len(data),
                    'error': str(e),
                },
            )
            raise TransportError('Send failed: %s' % e)

        self._pending.add(seq, True, now=permit.now)

        log_event(
            _LOG,
            logging.DEBUG,
            'icmp.send',
            'ICMP echo request sent',
            lambda: {
                'corr_id': seq,
                'target': self._target_ip,
                'bytes': len(packet),
                'payload_bytes': len(data),
                'pending': pending_before + 1,
            },
        )
        return seq

    def recv(self, timeout=None):
        self._prune_stale()
        if timeout == 0:
            result = self._try_recv_drain(_DRAIN_LIMIT)
            if result[0] is not None:
                return result
            return (None, None)

        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout
        while True:
            if timeout is None:
                wait = None
            else:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return (None, None)
                wait = remaining
            if not self._select_ready(wait):
                if timeout is None:
                    continue
                return (None, None)
            result = self._try_recv_drain(_DRAIN_LIMIT)
            if result[0] is not None:
                return result

    def _select_ready(self, wait):
        try:
            ready, _, _ = select.select(self._sock_list, [], [], wait)
        except select.error as e:
            raise TransportError('Select failed: %s' % e)
        return ready

    def _try_recv_drain(self, limit):
        count = 0
        while True:
            if limit is not None and count >= limit:
                return (None, None)
            packet, addr = self._recv_packet()
            if packet is None:
                return (None, None)
            result = self._parse_response(packet, addr)
            if result[0] is not None:
                return result
            count += 1

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
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.recv_failed',
                'ICMP receive failed',
                lambda: {'error': str(e)},
            )
            raise TransportError('Receive failed: %s' % e)
        return (packet, addr)

    def _parse_response(self, packet, addr):
        result, reason = parse_icmp_echo(
            packet,
            expect_type=ICMP_ECHO_REPLY,
            expect_ident=self._icmp_id,
            validate_checksum=False,
        )
        if result is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.malformed_response',
                'ICMP response malformed',
                lambda: {
                    'bytes': len(packet),
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'reason': reason,
                },
            )
            return (None, None)

        _, _, seq, payload = result
        if len(payload) > self._recv_packet_mtu:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.oversize_response',
                'ICMP response oversized',
                lambda: {
                    'corr_id': seq,
                    'bytes': len(payload),
                    'recv_packet_mtu': self._recv_packet_mtu,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)

        pending = self._pending.pop(seq)
        if pending is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.missing_pending',
                'ICMP response missing pending entry',
                lambda: {
                    'corr_id': seq,
                    'bytes': len(payload),
                },
            )
            return (None, None)

        log_event(
            _LOG,
            logging.DEBUG,
            'icmp.recv',
            'ICMP echo reply received',
            lambda: {
                'corr_id': seq,
                'bytes': len(payload),
            },
        )
        return (seq, payload)

    def _prune_stale(self, now=None):
        if now is None:
            now = time_provider.now()
        stale = self._pending.prune(now=now)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.prune_stale',
                'Pruned stale ICMP requests',
                lambda: {'count': len(stale)},
            )
        return stale

    def _next_sequence(self):
        for _ in range(0x10000):
            seq = self._next_seq
            self._next_seq = (self._next_seq + 1) & 0xFFFF
            if self._pending.get(seq) is None:
                return seq
        raise TransportError('No available ICMP sequence numbers')

    def _resolve_target(self, target):
        try:
            infos = socket.getaddrinfo(target, None, socket.AF_INET,
                                       socket.SOCK_DGRAM)
        except socket.gaierror as e:
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.resolve_failed',
                'Failed to resolve icmp_target',
                lambda: {
                    'target': target,
                    'error': str(e),
                },
            )
            raise TransportError('Failed to resolve icmp_target: %s' % target)
        if not infos:
            log_event(
                _LOG,
                logging.WARNING,
                'icmp.resolve_failed',
                'No IPv4 address for icmp_target',
                lambda: {'target': target},
            )
            raise TransportError('No IPv4 address for icmp_target: %s' % target)
        ip = infos[0][4][0]
        log_event(
            _LOG,
            logging.DEBUG,
            'icmp.resolve_target',
            'Resolved icmp target',
            lambda: {
                'target': target,
                'ip': ip,
            },
        )
        return ip

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

    def close(self):
        self._pending.clear()
        if self._sock:
            self._sock.close()
            self._sock = None
            self._sock_list = None
