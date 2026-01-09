# -*- coding: ascii -*-
"""
UDP ephemeral client transport for Alice.
"""

from __future__ import absolute_import

import errno
import logging
import select
import socket

from ..transport_base import (
    Transport,
    TransportError,
    PendingTracker,
    prune_and_count,
)
from ..mtu_limits import resolve_mtu_limits
from .udp_ephemeral_config import validate_udp_ephemeral_config
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)

_SOFT_RECV_ERRORS = set([errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH])
for name in ('WSAECONNREFUSED', 'WSAECONNRESET', 'WSAENETUNREACH', 'WSAEHOSTUNREACH'):
    value = getattr(errno, name, None)
    if value is not None:
        _SOFT_RECV_ERRORS.add(value)


def _get_errno(exc):
    err = getattr(exc, 'errno', None)
    if err is None and getattr(exc, 'args', None):
        if exc.args:
            try:
                err = int(exc.args[0])
            except (TypeError, ValueError):
                err = None
    return err


class _PendingRequest(object):
    __slots__ = ('sock', 'local_port', 'send_time')

    def __init__(self, sock, local_port, send_time):
        self.sock = sock
        self.local_port = local_port
        self.send_time = send_time


class UdpEphemeralClient(Transport):
    """
    UDP ephemeral client transport for Alice.

    Uses a fresh UDP socket per request and enforces source port reuse cooldown.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(UdpEphemeralClient, self).__init__()

        validated = validate_udp_ephemeral_config(config, role='client')
        self._config = config
        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'udp_ephemeral', config, role='client'
        )
        self._send_packet_mtu = send_mtu
        self._recv_packet_mtu = recv_mtu
        self._max_in_flight = config.max_in_flight
        self._pending_timeout = validated['pending_timeout']
        self._reuse_seconds = validated['reuse_seconds']

        target_host, target_port = validated['target_addr']
        self._target_addr = self._resolve_target(target_host, target_port)

        self._pending = PendingTracker(self._pending_timeout)
        self._sock_to_corr = {}
        self._port_last_used = {}
        self._next_corr_id = 0
        self._max_port_bind_attempts = 100
        self._recv_bufsize = max(1, self._recv_packet_mtu + 1)

        mtu_details = {
            'transport': 'udp_ephemeral',
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
            'udp_ephemeral.client_config',
            'UDP ephemeral client config',
            lambda: {
                'target': '%s:%d' % (target_host, target_port),
                'target_ip': '%s:%d' % (self._target_addr[0], self._target_addr[1]),
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'source_port_reuse_seconds': self._reuse_seconds,
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
            self._pending, self._prune_stale, now=now, on_prune=self._on_prune
        )
        self._ensure_reserved()
        reserved = len(self._reserved)
        pending_total = pending_before + reserved
        if pending_total >= self._max_in_flight:
            log_event(
                _LOG,
                logging.DEBUG,
                'udp_ephemeral.send_blocked',
                'UDP ephemeral send blocked',
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

        now = permit.now
        sock, local_port = self._create_socket(now)

        corr_id = self._next_corr_id
        self._next_corr_id = (self._next_corr_id + 1) & 0x7FFFFFFF

        try:
            sock.send(data)
        except socket.error as e:
            self._close_socket(sock, local_port, now)
            log_event(
                _LOG,
                logging.WARNING,
                'udp_ephemeral.send_failed',
                'UDP ephemeral send failed',
                lambda: {
                    'target': '%s:%d' % (
                        self._target_addr[0], self._target_addr[1]
                    ),
                    'bytes': len(data),
                    'local_port': local_port,
                    'error': str(e),
                },
            )
            raise TransportError('Send failed: %s' % e)

        state = _PendingRequest(sock, local_port, now)
        self._pending.add(corr_id, state, now=permit.now)
        self._sock_to_corr[sock] = corr_id

        log_event(
            _LOG,
            logging.DEBUG,
            'udp_ephemeral.send',
            'UDP ephemeral request sent',
            lambda: {
                'corr_id': corr_id,
                'bytes': len(data),
                'pending': pending_before + 1,
                'local_port': local_port,
                'target': '%s:%d' % (
                    self._target_addr[0], self._target_addr[1]
                ),
            },
        )
        return corr_id

    def recv(self, timeout=None):
        prune_and_count(
            self._pending, self._prune_stale, on_prune=self._on_prune
        )
        if not self._sock_to_corr:
            return (None, None)

        if timeout == 0:
            ready = self._select_ready(0)
            if ready:
                return self._try_recv_ready(ready)
            return (None, None)

        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout
        while True:
            if not self._sock_to_corr:
                return (None, None)
            if timeout is None:
                wait = None
            else:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return (None, None)
                wait = remaining
            ready = self._select_ready(wait)
            if not ready:
                if timeout is None:
                    continue
                return (None, None)
            result = self._try_recv_ready(ready)
            if result[0] is not None:
                return result
            if not self._sock_to_corr:
                return (None, None)

    def _try_recv_ready(self, ready):
        for sock in ready:
            result = self._try_recv_socket(sock)
            if result is not None:
                return result
        return (None, None)

    def _select_ready(self, wait):
        self._prune_invalid_sockets()
        if not self._sock_to_corr:
            return []
        try:
            ready, _, _ = select.select(
                list(self._sock_to_corr.keys()), [], [], wait
            )
            return ready
        except select.error as e:
            if self._prune_invalid_sockets() > 0:
                if not self._sock_to_corr:
                    return []
                try:
                    ready, _, _ = select.select(
                        list(self._sock_to_corr.keys()), [], [], wait
                    )
                    return ready
                except select.error as retry_exc:
                    e = retry_exc
            raise TransportError('Select failed: %s' % e)

    def _try_recv_socket(self, sock):
        corr_id = self._sock_to_corr.get(sock)
        if corr_id is None:
            return None
        state = self._pending.get(corr_id)
        if state is None:
            self._sock_to_corr.pop(sock, None)
            local_port = self._safe_get_port(sock)
            self._close_socket(sock, local_port, time_provider.now())
            return None
        try:
            data = sock.recv(self._recv_bufsize)
        except socket.error as e:
            err = _get_errno(e)
            if err in _SOFT_RECV_ERRORS:
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'udp_ephemeral.recv_refused',
                    'UDP ephemeral receive refused',
                    lambda: {
                        'corr_id': corr_id,
                        'local_port': state.local_port,
                        'error': str(e),
                    },
                )
                now = time_provider.now()
                self._drop_pending(corr_id, state, now)
                return None
            log_event(
                _LOG,
                logging.WARNING,
                'udp_ephemeral.recv_failed',
                'UDP ephemeral receive failed',
                lambda: {
                    'corr_id': corr_id,
                    'local_port': state.local_port,
                    'error': str(e),
                },
            )
            raise TransportError('Receive failed: %s' % e)

        now = time_provider.now()
        self._drop_pending(corr_id, state, now)

        if len(data) > self._recv_packet_mtu:
            log_event(
                _LOG,
                logging.DEBUG,
                'udp_ephemeral.oversize_response',
                'UDP ephemeral response oversized',
                lambda: {
                    'corr_id': corr_id,
                    'bytes': len(data),
                    'recv_packet_mtu': self._recv_packet_mtu,
                },
            )
            return None

        log_event(
            _LOG,
            logging.DEBUG,
            'udp_ephemeral.recv',
            'UDP ephemeral response received',
            lambda: {
                'corr_id': corr_id,
                'bytes': len(data),
            },
        )
        return (corr_id, data)

    def _drop_pending(self, corr_id, state, now, already_pruned=False):
        if not already_pruned:
            self._pending.pop(corr_id, None)
        if state is None:
            return
        sock = state.sock
        if sock is None:
            self._record_port_use(state.local_port, now)
            return
        self._sock_to_corr.pop(sock, None)
        state.sock = None
        self._close_socket(sock, state.local_port, now)

    def _prune_stale(self, now=None):
        if now is None:
            now = time_provider.now()
        stale = self._pending.prune(now=now)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'udp_ephemeral.prune_stale',
                'Pruned stale UDP ephemeral requests',
                lambda: {'count': len(stale)},
            )
        return stale

    def _prune_invalid_sockets(self, now=None):
        if not self._sock_to_corr:
            return 0
        if now is None:
            now = time_provider.now()
        invalid = []
        for sock, corr_id in list(self._sock_to_corr.items()):
            if not _socket_is_valid(sock):
                invalid.append((sock, corr_id))
        if not invalid:
            return 0
        for sock, corr_id in invalid:
            state = self._pending.get(corr_id)
            if state is None:
                self._sock_to_corr.pop(sock, None)
                local_port = self._safe_get_port(sock)
                self._close_socket(sock, local_port, now)
                continue
            self._drop_pending(corr_id, state, now)
        log_event(
            _LOG,
            logging.DEBUG,
            'udp_ephemeral.prune_invalid',
            'Pruned invalid UDP ephemeral sockets',
            lambda: {'count': len(invalid)},
        )
        return len(invalid)

    def _on_prune(self, stale):
        if not stale:
            return
        now = time_provider.now()
        for corr_id, state in stale:
            self._drop_pending(corr_id, state, now, already_pruned=True)

    def _create_socket(self, now):
        if self._reuse_seconds > 0:
            self._prune_port_history(now)
        for _ in range(self._max_port_bind_attempts):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                sock.bind(('', 0))
            except socket.error as e:
                sock.close()
                raise TransportError('Bind failed: %s' % e)
            local_port = sock.getsockname()[1]
            if self._reuse_seconds > 0:
                last_used = self._port_last_used.get(local_port)
                if last_used is not None:
                    if now - last_used < self._reuse_seconds:
                        sock.close()
                        continue
            try:
                sock.connect(self._target_addr)
            except socket.error as e:
                sock.close()
                raise TransportError('Connect failed: %s' % e)
            return sock, local_port
        log_event(
            _LOG,
            logging.WARNING,
            'udp_ephemeral.port_reuse_exhausted',
            'UDP ephemeral source ports exhausted',
            lambda: {
                'attempts': self._max_port_bind_attempts,
                'reuse_seconds': self._reuse_seconds,
            },
        )
        raise TransportError(
            'No available UDP source port after %d attempts' %
            self._max_port_bind_attempts
        )

    def _close_socket(self, sock, local_port, now):
        try:
            sock.close()
        finally:
            self._record_port_use(local_port, now)

    def _record_port_use(self, port, now):
        if port is None or self._reuse_seconds <= 0:
            return
        if now is None:
            now = time_provider.now()
        self._port_last_used[port] = now
        self._prune_port_history(now)

    def _prune_port_history(self, now):
        if self._reuse_seconds <= 0:
            self._port_last_used.clear()
            return
        cutoff = now - self._reuse_seconds
        for port, last_used in list(self._port_last_used.items()):
            if last_used <= cutoff:
                del self._port_last_used[port]

    def _resolve_target(self, host, port):
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET,
                                       socket.SOCK_DGRAM)
        except socket.gaierror as e:
            raise TransportError(
                'Failed to resolve udp_ephemeral_target %s:%s: %s' %
                (host, port, e)
            )
        if not infos:
            raise TransportError(
                'No IPv4 address for udp_ephemeral_target %s:%s' %
                (host, port)
            )
        return infos[0][4]

    def _safe_get_port(self, sock):
        try:
            return sock.getsockname()[1]
        except socket.error:
            return None

    def close(self):
        now = time_provider.now()
        for sock, corr_id in list(self._sock_to_corr.items()):
            state = self._pending.pop(corr_id, None)
            self._sock_to_corr.pop(sock, None)
            if state is None:
                local_port = self._safe_get_port(sock)
                self._close_socket(sock, local_port, now)
            else:
                self._close_socket(sock, state.local_port, now)
        self._pending.clear()
        self._sock_to_corr.clear()
        self._port_last_used.clear()


def _socket_is_valid(sock):
    if sock is None:
        return False
    try:
        return sock.fileno() >= 0
    except Exception:
        return False
