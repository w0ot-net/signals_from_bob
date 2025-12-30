# -*- coding: ascii -*-
"""
ICMP client transport for Alice.
"""

from __future__ import absolute_import

import logging
import os
import random
import select
import socket
import time

from ..transport_base import Transport, TransportError, PendingTracker, TokenBucket
from .icmp_packet import ICMP_ECHO_REPLY, build_echo_request, parse_icmp_echo
from ...compat import require_bytes
from ...config import Config
from ...logging_util import get_logger, log_event

_LOG = get_logger(__name__)


class IcmpClient(Transport):
    """
    ICMP client transport for Alice.

    Sends ICMP Echo Requests carrying SFB packets and receives Echo Replies.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        self._config = config
        self._require_privileges()

        if not config.icmp_target:
            raise TransportError('icmp_target required for ICMP transport')

        self._target_ip = self._resolve_target(config.icmp_target)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_RAW,
                                   socket.IPPROTO_ICMP)
        self._sock.setblocking(False)

        self._send_mtu = config.icmp_payload_mtu
        self._recv_mtu = config.icmp_payload_mtu
        self._max_pending = config.icmp_max_pending
        self._pending_timeout = config.icmp_pending_timeout
        self._recv_bufsize = 65535

        self._pending = PendingTracker(self._pending_timeout)
        self._next_seq = random.randint(0, 0xFFFF)
        self._icmp_id = random.randint(0, 0xFFFF)

        self._rate_limiter = None
        if config.icmp_send_interval and config.icmp_send_interval > 0:
            rate = 1.0 / float(config.icmp_send_interval)
            self._rate_limiter = TokenBucket(rate, capacity=1.0)

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
        self._prune_stale()
        return len(self._pending)

    def can_send(self):
        if self.pending_count() >= self._max_pending:
            return False
        if self._rate_limiter is None:
            return True
        return self._rate_limiter.can_take(1.0, now=time.time())

    def send(self, data):
        if not self.can_send():
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.send_blocked',
                'ICMP send blocked',
                {
                    'pending': self.pending_count(),
                    'max_pending': self._max_pending,
                },
            )
            raise TransportError('Rate limit exceeded or too many pending requests')

        data = require_bytes(data)
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )

        self._prune_stale()
        seq = self._next_sequence()
        packet = build_echo_request(self._icmp_id, seq, data)

        try:
            self._sock.sendto(packet, (self._target_ip, 0))
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        self._pending.add(seq, True)
        if self._rate_limiter is not None:
            self._rate_limiter.take(1.0, now=time.time())

        log_event(
            _LOG,
            logging.DEBUG,
            'icmp.send',
            'ICMP echo request sent',
            {
                'corr_id': seq,
                'target': self._target_ip,
                'bytes': len(packet),
                'payload_bytes': len(data),
                'pending': self.pending_count(),
            },
        )
        return seq

    def recv(self, timeout=None):
        self._prune_stale()
        if timeout == 0:
            try:
                ready, _, _ = select.select([self._sock], [], [], 0)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if ready:
                return self._try_recv()
            return (None, None)

        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout
        while True:
            if timeout is None:
                wait = None
            else:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return (None, None)
                wait = remaining
            try:
                ready, _, _ = select.select([self._sock], [], [], wait)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if not ready:
                if timeout is None:
                    continue
                return (None, None)
            result = self._try_recv()
            if result[0] is not None:
                return result

    def _try_recv(self):
        try:
            packet, addr = self._sock.recvfrom(self._recv_bufsize)
        except socket.error as e:
            raise TransportError('Receive failed: %s' % e)

        result = parse_icmp_echo(packet, expect_type=ICMP_ECHO_REPLY)
        if result is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.malformed_response',
                'ICMP response malformed',
                {
                    'bytes': len(packet),
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)

        _, ident, seq, payload = result
        if ident != self._icmp_id:
            return (None, None)
        if len(payload) > self._recv_mtu:
            return (None, None)

        pending = self._pending.pop(seq)
        if pending is None:
            return (None, None)

        log_event(
            _LOG,
            logging.DEBUG,
            'icmp.recv',
            'ICMP echo reply received',
            {
                'corr_id': seq,
                'bytes': len(payload),
            },
        )
        return (seq, payload)

    def _prune_stale(self, now=None):
        if now is None:
            now = time.time()
        stale = self._pending.prune(now=now)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'icmp.prune_stale',
                'Pruned stale ICMP requests',
                {'count': len(stale)},
            )

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
        except socket.gaierror:
            raise TransportError('Failed to resolve icmp_target: %s' % target)
        if not infos:
            raise TransportError('No IPv4 address for icmp_target: %s' % target)
        return infos[0][4][0]

    def _require_privileges(self):
        if os.name != 'posix':
            raise TransportError('ICMP transport requires Linux raw sockets')
        if not hasattr(os, 'geteuid') or os.geteuid() != 0:
            raise TransportError('ICMP transport requires root privileges')

    def close(self):
        self._pending.clear()
        if self._sock:
            self._sock.close()
            self._sock = None
