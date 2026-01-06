# -*- coding: ascii -*-
"""
TLS ClientHello transport for Alice.
"""

from __future__ import absolute_import

import logging
import select
import socket

from ..transport_base import (
    Transport,
    TransportError,
    PendingTracker,
    _get_errno,
    prune_and_count,
)
from ..proxy_helpers import (
    build_connect_request,
    ProxyConnect,
    PROXY_CLOSED,
    PROXY_DONE,
)
from ..socket_errors import (
    IN_PROGRESS_ERRNOS,
    TEMP_ERRORS,
    SOFT_CONNECT_ERRORS,
    RESET_ERRORS,
    PHASE_CONNECT,
    PHASE_PROXY,
    PHASE_REQUEST,
    PHASE_RESPONSE,
)
from . import tls_handshake_codec as codec
from .tls_handshake_config import validate_tls_config
from ...utils import parse_host_port
from ...compat import buffer_view, require_bytes_like, to_bytes
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)


class _PendingConn(object):
    __slots__ = (
        'sock',
        'phase',
        'send_buf',
        'send_off',
        'recv_buf',
        'record_len',
        'connect_deadline',
        'handshake_deadline',
        'proxy_state',
    )

    def __init__(self, sock, send_buf, connect_deadline, proxy_state=None):
        self.sock = sock
        self.phase = PHASE_CONNECT
        self.send_buf = send_buf
        self.send_off = 0
        self.recv_buf = bytearray()
        self.record_len = None
        self.connect_deadline = connect_deadline
        self.handshake_deadline = None
        self.proxy_state = proxy_state


class TlsClient(Transport):
    """
    TLS ClientHello transport for Alice.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(TlsClient, self).__init__()
        self._config = config
        validated = validate_tls_config(config, 'client')

        self._pending_timeout = validated['pending_timeout']
        self._connect_timeout = validated['connect_timeout']
        self._handshake_timeout = validated['handshake_timeout']
        self._max_record_send = validated['max_clienthello_bytes']
        self._max_record_recv = validated['max_serverhello_bytes']
        self._send_packet_mtu = validated['client_payload_cap']
        self._recv_packet_mtu = validated['server_payload_cap']
        self._sni = validated['sni']
        self._alpn_list = validated['alpn_list']
        self._clienthello_padding_target = validated['clienthello_padding_target']
        self._proxy_timeout = validated['proxy_timeout']

        self._max_in_flight = config.max_in_flight
        target_host, target_port = parse_host_port(config.tls_target)
        self._target_host = target_host
        self._target_port = target_port
        self._target_addr = None
        self._proxy_addr = None
        self._proxy_label = None
        self._proxy_auth = None
        self._proxy_request = None
        if config.tls_http_proxy is not None:
            proxy_host, proxy_port = parse_host_port(config.tls_http_proxy)
            self._proxy_addr = self._resolve_target(
                proxy_host, proxy_port, 'tls_http_proxy'
            )
            self._proxy_label = '%s:%d' % (proxy_host, proxy_port)
            self._proxy_auth = config.tls_http_proxy_auth
            target_hostport = '%s:%d' % (self._target_host, self._target_port)
            self._proxy_request = build_connect_request(
                target_hostport,
                proxy_auth=self._proxy_auth,
            )
            self._connect_addr = self._proxy_addr
        else:
            self._target_addr = self._resolve_target(
                target_host, target_port, 'tls_target'
            )
            self._connect_addr = self._target_addr

        if self._target_addr is not None:
            target_desc = '%s:%d' % (self._target_addr[0], self._target_addr[1])
        else:
            target_desc = '%s:%d' % (self._target_host, self._target_port)
        log_event(
            _LOG,
            logging.INFO,
            'tls.client_config',
            'TLS client config',
            lambda: {
                'target': target_desc,
                'proxy': self._proxy_label,
                'proxy_timeout': self._proxy_timeout,
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'connect_timeout': self._connect_timeout,
                'handshake_timeout': self._handshake_timeout,
                'max_clienthello_bytes': self._max_record_send,
                'max_serverhello_bytes': self._max_record_recv,
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
                'sni': self._sni,
                'alpn': self._alpn_list,
                'clienthello_padding_target': self._clienthello_padding_target,
            },
        )

        self._pending = PendingTracker(self._pending_timeout)
        self._pending_state = {}
        self._sock_to_corr = {}
        self._next_corr_id = 0

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def max_in_flight(self):
        return self._max_in_flight

    def pending_count(self):
        return len(self._pending_state)

    def reserve_send(self, now=None):
        if now is None:
            now = time_provider.now()
        self._prune_deadlines(now=now)
        pending_before = prune_and_count(
            self._pending,
            self._pending.prune,
            now=now,
            on_prune=self._on_prune,
        )
        self._ensure_reserved()
        reserved = len(self._reserved)
        pending_total = pending_before + reserved
        if pending_total >= self._max_in_flight:
            log_event(
                _LOG,
                logging.DEBUG,
                'tls.send_blocked',
                'TLS send blocked',
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
            pending_before = len(self._pending_state)
        require_bytes_like(data)
        data = to_bytes(data)
        if len(data) > self._send_packet_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (
                    len(data), self._send_packet_mtu
                )
            )

        try:
            record = codec.build_client_hello_record(
                data,
                sni=self._sni,
                alpn_list=self._alpn_list,
                padding_target=self._clienthello_padding_target,
            )
        except ValueError as exc:
            raise TransportError('TLS encode failed: %s' % exc)
        if len(record) > self._max_record_send:
            raise TransportError('TLS record exceeds configured max')

        corr_id = self._next_corr_id
        self._next_corr_id += 1

        sock = self._create_socket()
        now = permit.now
        proxy_state = None
        if self._proxy_request is not None:
            proxy_state = ProxyConnect(
                sock,
                self._proxy_request,
                _get_errno,
                TEMP_ERRORS,
                lambda reason, **extra: self._log_proxy_error(
                    reason, corr_id, **extra
                ),
            )
        state = _PendingConn(
            sock=sock,
            send_buf=record,
            connect_deadline=now + self._connect_timeout,
            proxy_state=proxy_state,
        )
        self._pending_state[corr_id] = state
        self._pending.add(corr_id, True, now=now)
        self._sock_to_corr[sock] = corr_id

        err = sock.connect_ex(self._connect_addr)
        if err == 0:
            self._handle_connect_success(corr_id, state, now)
        elif err in IN_PROGRESS_ERRNOS:
            state.phase = PHASE_CONNECT
        else:
            self._close_pending(corr_id, state)
            log_event(
                _LOG,
                logging.WARNING,
                'tls.connect_error',
                'TLS connect error',
                lambda: {'error': err},
            )
            if err in SOFT_CONNECT_ERRORS:
                return corr_id
            raise TransportError('TLS connect failed: %s' % err)

        log_event(
            _LOG,
            logging.DEBUG,
            'tls.send',
            'TLS ClientHello queued',
            lambda: {
                'corr_id': corr_id,
                'payload_bytes': len(data),
                'record_bytes': len(record),
                'pending': pending_before + 1,
            },
        )
        return corr_id

    def recv(self, timeout=None):
        self._prune_deadlines()
        if not self._pending_state:
            return (None, None)

        deadline = None
        if timeout is not None and timeout > 0:
            deadline = time_provider.now() + timeout

        while True:
            now = time_provider.now()
            self._prune_deadlines(now=now)
            if not self._pending_state:
                return (None, None)

            wait = self._select_timeout(now, deadline, timeout)
            read_list, write_list = self._build_select_lists()
            try:
                ready_r, ready_w, _ = select.select(read_list, write_list, [], wait)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)

            if not ready_r and not ready_w:
                if timeout == 0:
                    return (None, None)
                if deadline is not None and time_provider.now() >= deadline:
                    return (None, None)
                continue

            ready = set(ready_r)
            ready.update(ready_w)
            for sock in ready:
                result = self._drive_socket(
                    sock,
                    now,
                    sock in ready_r,
                    sock in ready_w,
                )
                if result is not None:
                    return result

            if timeout == 0:
                return (None, None)
            if deadline is not None and time_provider.now() >= deadline:
                return (None, None)

    def _drive_socket(self, sock, now, can_read, can_write):
        corr_id, state = self._lookup_state(sock)
        if state is None:
            return None
        if state.phase == PHASE_PROXY:
            status = state.proxy_state.drive(can_read, can_write, now)
            if status == PROXY_DONE:
                state.proxy_state = None
                state.phase = PHASE_REQUEST
            elif status == PROXY_CLOSED:
                self._close_pending(corr_id, state)
            return None
        if can_write:
            result = self._drive_write(corr_id, state, now)
            if result is not None:
                return result
        if can_read:
            result = self._drive_read(corr_id, state)
            if result is not None:
                return result
        return None

    def _drive_write(self, corr_id, state, now):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return self._finish_connect(corr_id, state, now)
        if phase == PHASE_REQUEST:
            if state.connect_deadline is None:
                state.connect_deadline = now + self._connect_timeout
            if self._flush_send(corr_id, state, now):
                state.phase = PHASE_RESPONSE
                state.connect_deadline = None
                state.handshake_deadline = now + self._handshake_timeout
            return None
        return None

    def _drive_read(self, corr_id, state):
        phase = state.phase
        if phase == PHASE_RESPONSE:
            return self._recv_record(corr_id, state)
        return None

    def _finish_connect(self, corr_id, state, now):
        err = state.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err != 0:
            self._close_pending(corr_id, state)
            log_event(
                _LOG,
                logging.WARNING,
                'tls.connect_error',
                'TLS connect error',
                lambda: {'error': err},
            )
            if err in SOFT_CONNECT_ERRORS:
                return None
            raise TransportError('TLS connect failed: %s' % err)
        self._handle_connect_success(corr_id, state, now)
        return None

    def _lookup_state(self, sock):
        corr_id = self._sock_to_corr.get(sock)
        if corr_id is None:
            return None, None
        state = self._pending_state.get(corr_id)
        if state is None:
            return None, None
        return corr_id, state

    def _handle_connect_success(self, corr_id, state, now):
        if state.proxy_state is not None:
            state.phase = PHASE_PROXY
            state.connect_deadline = None
            if self._proxy_timeout is not None:
                state.proxy_state.set_deadline(now + self._proxy_timeout)
            state.proxy_state.drive(False, True, now)
        else:
            state.phase = PHASE_REQUEST
            if self._flush_send(corr_id, state, now):
                state.phase = PHASE_RESPONSE
                state.connect_deadline = None
                state.handshake_deadline = now + self._handshake_timeout

    def _phase_deadline(self, state):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return state.connect_deadline
        if phase == PHASE_PROXY:
            return state.proxy_state.deadline()
        if phase == PHASE_REQUEST:
            return state.connect_deadline
        if phase == PHASE_RESPONSE:
            return state.handshake_deadline
        return None

    def _phase_interests(self, state):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return False, True
        if phase == PHASE_PROXY:
            if state.proxy_state is None:
                return False, False
            return (state.proxy_state.wants_read(),
                    state.proxy_state.wants_write())
        if phase == PHASE_REQUEST:
            return False, True
        if phase == PHASE_RESPONSE:
            return True, False
        return False, False

    def _recv_record(self, corr_id, state):
        bufsize = 4096
        if state.record_len is not None:
            expected = codec.TLS_RECORD_HEADER_LEN + state.record_len
            remaining = expected - len(state.recv_buf)
            if remaining <= 0:
                remaining = 1
            bufsize = min(bufsize, remaining + 1)
        try:
            data = state.sock.recv(bufsize)
        except socket.error as e:
            err = _get_errno(e)
            if err in TEMP_ERRORS:
                return None
            if err in RESET_ERRORS:
                self._close_pending(corr_id, state)
                return None
            self._close_pending(corr_id, state)
            raise TransportError('Receive failed: %s' % e)
        if not data:
            self._log_parse_error('tls.eof', corr_id)
            self._close_pending(corr_id, state)
            return None
        state.recv_buf.extend(data)
        if state.record_len is None and len(state.recv_buf) >= codec.TLS_RECORD_HEADER_LEN:
            try:
                state.record_len = codec.parse_record_header(
                    state.recv_buf[:codec.TLS_RECORD_HEADER_LEN],
                    max_record_bytes=self._max_record_recv,
                )
            except ValueError:
                self._log_parse_error('tls.header', corr_id)
                self._close_pending(corr_id, state)
                return None
        if state.record_len is None:
            return None
        expected = codec.TLS_RECORD_HEADER_LEN + state.record_len
        if len(state.recv_buf) > expected:
            self._log_parse_error('tls.extra', corr_id)
            self._close_pending(corr_id, state)
            return None
        if len(state.recv_buf) < expected:
            return None
        try:
            payload, _cipher = codec.parse_server_hello_record(
                to_bytes(state.recv_buf),
                max_record_bytes=self._max_record_recv,
            )
        except ValueError:
            self._log_parse_error('tls.parse', corr_id)
            self._close_pending(corr_id, state)
            return None
        if len(payload) > self._recv_packet_mtu:
            self._log_parse_error('tls.mtu', corr_id)
            self._close_pending(corr_id, state)
            return None
        self._close_pending(corr_id, state)
        log_event(
            _LOG,
            logging.DEBUG,
            'tls.recv',
            'TLS ServerHello received',
            lambda: {
                'corr_id': corr_id,
                'payload_bytes': len(payload),
            },
        )
        return (corr_id, payload)

    def _prune_deadlines(self, now=None):
        if now is None:
            now = time_provider.now()
        stale = []
        for corr_id, state in list(self._pending_state.items()):
            deadline = self._phase_deadline(state)
            if deadline is not None and now > deadline:
                if state.phase == PHASE_PROXY:
                    self._log_proxy_error('timeout', corr_id)
                stale.append((corr_id, state))
        for corr_id, state in stale:
            self._close_pending(corr_id, state)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'tls.prune_stale',
                'Pruned stale TLS connections',
                lambda: {'count': len(stale)},
            )
        return stale

    def _on_prune(self, stale):
        for corr_id, _value in stale:
            state = self._pending_state.get(corr_id)
            if state is not None:
                self._close_pending(corr_id, state)


    def _flush_send(self, corr_id, state, now):
        if state.send_off >= len(state.send_buf):
            return True
        view = buffer_view(state.send_buf)
        try:
            sent = state.sock.send(view[state.send_off:])
        except socket.error as e:
            if _get_errno(e) in TEMP_ERRORS:
                return False
            self._close_pending(corr_id, state)
            raise TransportError('Send failed: %s' % e)
        if sent <= 0:
            self._close_pending(corr_id, state)
            raise TransportError('Send failed: connection closed')
        state.send_off += sent
        return state.send_off >= len(state.send_buf)

    def _close_pending(self, corr_id, state):
        self._pending_state.pop(corr_id, None)
        self._pending.pop(corr_id, None)
        if state.sock is not None:
            self._sock_to_corr.pop(state.sock, None)
            try:
                state.sock.close()
            except Exception:
                pass
            state.sock = None

    def _select_timeout(self, now, deadline, timeout):
        earliest = None
        for state in self._pending_state.values():
            state_deadline = self._phase_deadline(state)
            if state_deadline is not None:
                if earliest is None or state_deadline < earliest:
                    earliest = state_deadline
        if timeout == 0:
            return 0
        if deadline is not None:
            remaining = deadline - now
            if remaining <= 0:
                return 0
            if earliest is not None:
                return max(0, min(remaining, earliest - now))
            return remaining
        if earliest is not None:
            return max(0, earliest - now)
        return None

    def _build_select_lists(self):
        read_list = []
        write_list = []
        for state in self._pending_state.values():
            want_read, want_write = self._phase_interests(state)
            if want_read:
                read_list.append(state.sock)
            if want_write:
                write_list.append(state.sock)
        return read_list, write_list

    def _resolve_target(self, host, port, label):
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET,
                                       socket.SOCK_STREAM)
        except socket.gaierror:
            raise TransportError('Failed to resolve %s: %s' % (label, host))
        if not infos:
            raise TransportError('No IPv4 address for %s: %s' % (label, host))
        return infos[0][4]

    def _create_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        return sock

    def _log_proxy_error(self, reason, corr_id, **extra):
        def _fields():
            data = {'reason': reason, 'corr_id': corr_id}
            data.update(extra)
            return data
        log_event(
            _LOG,
            logging.WARNING,
            'tls.proxy_error',
            'TLS proxy error',
            _fields,
        )

    def _log_parse_error(self, reason, corr_id):
        log_event(
            _LOG,
            logging.DEBUG,
            'tls.parse_error',
            'TLS parse error',
            lambda: {'reason': reason, 'corr_id': corr_id},
        )

    def close(self):
        for corr_id, state in list(self._pending_state.items()):
            self._close_pending(corr_id, state)
        self._pending.clear()
