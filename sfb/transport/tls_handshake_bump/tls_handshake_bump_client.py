# -*- coding: ascii -*-
"""
TLS handshake bump transport for Alice.
"""

from __future__ import absolute_import

import logging
import socket
import ssl

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
    PHASE_HANDSHAKE,
    PHASE_REQUEST,
    PHASE_RESPONSE,
)
from . import tls_handshake_bump_codec as codec
from . import tls_handshake_bump_selector as bump_selector
from .tls_handshake_bump_config import validate_tls_bump_config
from ...utils import parse_host_port
from ...compat import buffer_view, require_bytes_like, text_type, to_bytes
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)


_SSL_WANT_READ = getattr(ssl, 'SSL_ERROR_WANT_READ', None)
_SSL_WANT_WRITE = getattr(ssl, 'SSL_ERROR_WANT_WRITE', None)
_SSL_WANT_READ_ERROR = getattr(ssl, 'SSLWantReadError', None)
_SSL_WANT_WRITE_ERROR = getattr(ssl, 'SSLWantWriteError', None)

_MAX_RESPONSE_BYTES = 65536

class _PendingConn(object):
    __slots__ = (
        'sock',
        'phase',
        'connect_deadline',
        'proxy_state',
        'sni_name',
        'request_buf',
        'request_off',
        'ssl_want',
        'handshake_deadline',
        'pending_deadline',
        'recv_buf',
        'scan_offset',
    )

    def __init__(self, sock, connect_deadline, pending_deadline, proxy_state,
                 sni_name, request_buf):
        self.sock = sock
        self.phase = PHASE_CONNECT
        self.connect_deadline = connect_deadline
        self.pending_deadline = pending_deadline
        self.proxy_state = proxy_state
        self.sni_name = sni_name
        self.request_buf = request_buf
        self.request_off = 0
        self.ssl_want = None
        self.handshake_deadline = None
        self.recv_buf = bytearray()
        self.scan_offset = 0


class TlsHandshakeBumpClient(Transport):
    """
    TLS handshake bump transport for Alice.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(TlsHandshakeBumpClient, self).__init__()
        self._config = config
        validated = validate_tls_bump_config(config, 'client')

        self._pending_timeout = validated['pending_timeout']
        self._connect_timeout = validated['connect_timeout']
        self._handshake_timeout = validated['handshake_timeout']
        self._send_mtu = validated['sni_payload_cap']
        self._recv_mtu = validated['cn_payload_cap']
        self._cn_max_len = validated['cn_max_len']
        self._base_domain = validated['base_domain']
        self._request_path = validated['request_path']
        self._base_domain_labels = self._base_domain.split('.')
        self._request_prefix, self._request_suffix = _build_https_request_parts(
            self._request_path
        )

        self._max_in_flight = config.max_in_flight
        target_host, target_port = parse_host_port(config.tls_bump_target)
        self._target_host = target_host
        self._target_port = target_port
        self._target_addr = None
        self._proxy_addr = None
        self._proxy_label = None
        self._proxy_auth = None
        self._proxy_request = None
        if config.tls_bump_http_proxy is not None:
            proxy_host, proxy_port = parse_host_port(config.tls_bump_http_proxy)
            self._proxy_addr = self._resolve_target(
                proxy_host, proxy_port, 'tls_bump_http_proxy'
            )
            self._proxy_label = '%s:%d' % (proxy_host, proxy_port)
            self._proxy_auth = config.tls_bump_http_proxy_auth
            target_hostport = '%s:%d' % (self._target_host, self._target_port)
            self._proxy_request = build_connect_request(
                target_hostport,
                proxy_auth=self._proxy_auth,
                target_label='tls_bump_target',
                proxy_label='tls_bump_http_proxy',
                proxy_auth_label='tls_bump_http_proxy_auth',
            )
            self._connect_addr = self._proxy_addr
        else:
            self._target_addr = self._resolve_target(
                target_host, target_port, 'tls_bump_target'
            )
            self._connect_addr = self._target_addr

        self._ssl_context = _create_ssl_context()
        self._selector = bump_selector.SocketSelector()

        if self._target_addr is not None:
            target_desc = '%s:%d' % (self._target_addr[0], self._target_addr[1])
        else:
            target_desc = '%s:%d' % (self._target_host, self._target_port)
        log_event(
            _LOG,
            logging.INFO,
            'tls_bump.client_config',
            'TLS bump client config',
            lambda: {
                'target': target_desc,
                'proxy': self._proxy_label,
                'proxy_timeout': validated['proxy_timeout'],
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'connect_timeout': self._connect_timeout,
                'handshake_timeout': self._handshake_timeout,
                'send_mtu': self._send_mtu,
                'recv_mtu': self._recv_mtu,
                'base_domain': self._base_domain,
                'request_path': self._request_path,
            },
        )

        self._pending = PendingTracker(self._pending_timeout)
        self._pending_state = {}
        self._sock_to_corr = {}
        self._next_corr_id = 0
        self._proxy_timeout = validated['proxy_timeout']

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
                'tls_bump.send_blocked',
                'TLS bump send blocked',
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
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )

        try:
            sni_name = codec.encode_sni_name_with_labels(
                data,
                self._base_domain_labels,
            )
        except ValueError as exc:
            raise TransportError('SNI encode failed: %s' % exc)
        try:
            request_buf = _build_https_request(
                sni_name,
                self._request_prefix,
                self._request_suffix,
            )
        except ValueError as exc:
            raise TransportError('Request build failed: %s' % exc)

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
            connect_deadline=now + self._connect_timeout,
            pending_deadline=now + self._pending_timeout,
            proxy_state=proxy_state,
            sni_name=sni_name,
            request_buf=request_buf,
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
                'tls_bump.connect_error',
                'TLS bump connect error',
                lambda: {'error': err},
            )
            if err in SOFT_CONNECT_ERRORS:
                return corr_id
            raise TransportError('TLS bump connect failed: %s' % err)

        log_event(
            _LOG,
            logging.DEBUG,
            'tls_bump.send',
            'TLS bump request queued',
            lambda: {
                'corr_id': corr_id,
                'payload_bytes': len(data),
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
            ready_r, ready_w = self._selector.wait(read_list, write_list, wait)

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
                self._start_handshake(corr_id, state, now)
            elif status == PROXY_CLOSED:
                self._close_pending(corr_id, state)
            return None
        if can_write:
            result = self._drive_write(corr_id, state, now)
            if result is not None:
                return result
        if can_read:
            result = self._drive_read(corr_id, state, now)
            if result is not None:
                return result
        return None

    def _drive_write(self, corr_id, state, now):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return self._finish_connect(corr_id, state, now)
        if phase == PHASE_HANDSHAKE:
            if state.handshake_deadline is None:
                state.handshake_deadline = now + self._handshake_timeout
            if state.ssl_want in (None, 'write'):
                self._do_handshake(corr_id, state)
            return None
        if phase == PHASE_REQUEST:
            if state.ssl_want in (None, 'write'):
                self._flush_request(corr_id, state)
            return None
        if phase == PHASE_RESPONSE:
            if state.ssl_want == 'write':
                return self._recv_response(corr_id, state)
        return None

    def _drive_read(self, corr_id, state, now):
        phase = state.phase
        if phase == PHASE_HANDSHAKE:
            if state.handshake_deadline is None:
                state.handshake_deadline = now + self._handshake_timeout
            if state.ssl_want in (None, 'read'):
                self._do_handshake(corr_id, state)
            return None
        if phase == PHASE_REQUEST:
            if state.ssl_want in (None, 'read'):
                self._flush_request(corr_id, state)
            return None
        if phase == PHASE_RESPONSE:
            if state.ssl_want in (None, 'read'):
                return self._recv_response(corr_id, state)
            return None
        return None

    def _finish_connect(self, corr_id, state, now):
        err = state.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err != 0:
            self._close_pending(corr_id, state)
            log_event(
                _LOG,
                logging.WARNING,
                'tls_bump.connect_error',
                'TLS bump connect error',
                lambda: {'error': err},
            )
            if err in SOFT_CONNECT_ERRORS:
                return None
            raise TransportError('TLS bump connect failed: %s' % err)
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
            self._start_handshake(corr_id, state, now)

    def _phase_deadline(self, state):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return state.connect_deadline
        if phase == PHASE_PROXY:
            return state.proxy_state.deadline()
        if phase == PHASE_HANDSHAKE:
            return state.handshake_deadline
        return state.pending_deadline

    def _phase_interests(self, state):
        phase = state.phase
        if phase == PHASE_CONNECT:
            return False, True
        if phase == PHASE_PROXY:
            if state.proxy_state is None:
                return False, False
            return (state.proxy_state.wants_read(),
                    state.proxy_state.wants_write())
        if phase == PHASE_HANDSHAKE:
            if state.ssl_want == 'write':
                return False, True
            if state.ssl_want == 'read':
                return True, False
            return True, True
        if phase == PHASE_REQUEST:
            if state.ssl_want == 'read':
                return True, False
            return False, True
        if phase == PHASE_RESPONSE:
            if state.ssl_want == 'write':
                return False, True
            return True, False
        return False, False

    def _start_handshake(self, corr_id, state, now):
        ssl_sock = _wrap_socket(self._ssl_context, state.sock, state.sni_name)
        self._sock_to_corr.pop(state.sock, None)
        state.sock = ssl_sock
        self._sock_to_corr[ssl_sock] = corr_id
        state.phase = PHASE_HANDSHAKE
        state.handshake_deadline = now + self._handshake_timeout
        state.ssl_want = None
        state.request_off = 0
        self._do_handshake(corr_id, state)

    def _do_handshake(self, corr_id, state):
        try:
            state.sock.do_handshake()
        except ssl.SSLError as e:
            if _ssl_wants_read(e):
                state.ssl_want = 'read'
                return None
            if _ssl_wants_write(e):
                state.ssl_want = 'write'
                return None
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump handshake failed: %s' % e)
        state.phase = PHASE_REQUEST
        state.handshake_deadline = None
        state.ssl_want = None
        return None


    def _flush_request(self, corr_id, state):
        if state.request_off >= len(state.request_buf):
            state.phase = PHASE_RESPONSE
            state.ssl_want = None
            return True
        view = buffer_view(state.request_buf)
        try:
            sent = state.sock.send(view[state.request_off:])
        except ssl.SSLError as e:
            if _ssl_wants_read(e):
                state.ssl_want = 'read'
                return False
            if _ssl_wants_write(e):
                state.ssl_want = 'write'
                return False
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump send failed: %s' % e)
        except socket.error as e:
            if _get_errno(e) in TEMP_ERRORS:
                return False
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump send failed: %s' % e)
        if sent <= 0:
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump send failed: connection closed')
        state.request_off += sent
        if state.request_off >= len(state.request_buf):
            state.phase = PHASE_RESPONSE
            state.ssl_want = None
            return True
        return False

    def _recv_response(self, corr_id, state):
        try:
            data = state.sock.recv(4096)
        except ssl.SSLError as e:
            if _ssl_wants_read(e):
                state.ssl_want = 'read'
                return None
            if _ssl_wants_write(e):
                state.ssl_want = 'write'
                return None
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump receive failed: %s' % e)
        except socket.error as e:
            err = _get_errno(e)
            if err in TEMP_ERRORS:
                return None
            if err in RESET_ERRORS:
                self._close_pending(corr_id, state)
                return None
            self._close_pending(corr_id, state)
            raise TransportError('TLS bump receive failed: %s' % e)
        if not data:
            self._log_parse_error('tls_bump.eof', corr_id)
            self._close_pending(corr_id, state)
            return None
        state.recv_buf.extend(data)
        if len(state.recv_buf) > _MAX_RESPONSE_BYTES:
            self._log_parse_error('tls_bump.response_too_large', corr_id)
            self._close_pending(corr_id, state)
            return None
        payload = self._extract_payload(state, corr_id)
        if payload is None:
            return None
        if len(payload) > self._recv_mtu:
            self._log_parse_error('tls_bump.mtu', corr_id)
            self._close_pending(corr_id, state)
            return None
        self._close_pending(corr_id, state)
        log_event(
            _LOG,
            logging.DEBUG,
            'tls_bump.recv',
            'TLS bump response received',
            lambda: {
                'corr_id': corr_id,
                'payload_bytes': len(payload),
            },
        )
        return (corr_id, payload)

    def _extract_payload(self, state, corr_id):
        buffer_bytes = state.recv_buf
        payload = codec.scan_response_payload(
            buffer_bytes,
            max_payload_len=self._recv_mtu,
            max_token_len=self._cn_max_len,
            start_offset=state.scan_offset,
        )
        if payload is None:
            lookback = self._cn_max_len if self._cn_max_len else 0
            if lookback:
                state.scan_offset = max(0, len(buffer_bytes) - lookback + 1)
            else:
                state.scan_offset = 0
            return None
        return payload

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
                'tls_bump.prune_stale',
                'Pruned stale TLS bump connections',
                lambda: {'count': len(stale)},
            )
        return stale

    def _on_prune(self, stale):
        for corr_id, _value in stale:
            state = self._pending_state.get(corr_id)
            if state is not None:
                self._close_pending(corr_id, state)

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
        # TODO: consider a small pre-connect pool to trim TCP setup latency.
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
            'tls_bump.proxy_error',
            'TLS bump proxy error',
            _fields,
        )

    def _log_parse_error(self, reason, corr_id):
        log_event(
            _LOG,
            logging.DEBUG,
            'tls_bump.parse_error',
            'TLS bump parse error',
            lambda: {'reason': reason, 'corr_id': corr_id},
        )

    def close(self):
        for corr_id, state in list(self._pending_state.items()):
            self._close_pending(corr_id, state)
        self._pending.clear()


def _build_https_request_parts(path):
    if not isinstance(path, text_type):
        raise ValueError('Path must be text')
    try:
        path_bytes = path.encode('ascii')
    except UnicodeError:
        raise ValueError('Path must be ASCII')
    prefix = b'GET ' + path_bytes + b' HTTP/1.1\r\nHost: '
    suffix = b'\r\nConnection: close\r\n\r\n'
    return prefix, suffix


def _build_https_request(sni_name, prefix, suffix):
    if not isinstance(sni_name, text_type):
        raise ValueError('SNI must be text')
    try:
        sni_bytes = sni_name.encode('ascii')
    except UnicodeError:
        raise ValueError('SNI must be ASCII')
    return prefix + sni_bytes + suffix


def _create_ssl_context():
    if not hasattr(ssl, 'SSLContext'):
        raise TransportError('SSLContext required for TLS bump transport')
    has_tls12 = getattr(ssl, 'HAS_TLSv1_2', False)
    proto_tls12 = getattr(ssl, 'PROTOCOL_TLSv1_2', None)
    if not has_tls12 and proto_tls12 is None:
        raise TransportError('TLS bump transport requires TLS 1.2 support')
    proto = getattr(ssl, 'PROTOCOL_TLS_CLIENT', None)
    if proto is None:
        if proto_tls12 is None:
            proto = getattr(ssl, 'PROTOCOL_TLSv1', ssl.PROTOCOL_SSLv23)
        else:
            proto = proto_tls12
    context = ssl.SSLContext(proto)
    if hasattr(context, 'check_hostname'):
        context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _wrap_socket(context, sock, sni_name):
    try:
        return context.wrap_socket(
            sock,
            server_hostname=sni_name,
            do_handshake_on_connect=False,
        )
    except TypeError:
        raise TransportError('TLS bump transport requires SNI support')


def _ssl_wants_read(exc):
    if _SSL_WANT_READ_ERROR is not None and isinstance(exc, _SSL_WANT_READ_ERROR):
        return True
    return getattr(exc, 'errno', None) == _SSL_WANT_READ


def _ssl_wants_write(exc):
    if _SSL_WANT_WRITE_ERROR is not None and isinstance(exc, _SSL_WANT_WRITE_ERROR):
        return True
    return getattr(exc, 'errno', None) == _SSL_WANT_WRITE
