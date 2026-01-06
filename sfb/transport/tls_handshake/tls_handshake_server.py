# -*- coding: ascii -*-
"""
TLS ClientHello server transport for Bob.
"""

from __future__ import absolute_import

import logging
import select
import socket

from ..transport_base import Server, TransportError, _get_errno, raise_bind_error
from ..socket_errors import TEMP_ERRORS
from . import tls_handshake_codec as codec
from .tls_handshake_config import validate_tls_config
from ...utils import parse_host_port
from ...compat import require_bytes_like, to_bytes
from ...config import Config
from ...logging_util import get_logger, log_event
from ... import time_provider

_LOG = get_logger(__name__)

class _ConnState(object):
    __slots__ = (
        'sock',
        'recv_buf',
        'record_len',
        'handshake_deadline',
        'ready_to_respond',
        'response_buf',
        'response_off',
        'cipher_suite',
        'responder_used',
    )

    def __init__(self, sock, handshake_deadline):
        self.sock = sock
        self.recv_buf = bytearray()
        self.record_len = None
        self.handshake_deadline = handshake_deadline
        self.ready_to_respond = False
        self.response_buf = None
        self.response_off = 0
        self.cipher_suite = None
        self.responder_used = False


class _ResponseSender(object):
    def __init__(self, server, state):
        self._server = server
        self._state = state

    def __call__(self, data):
        self._server._enqueue_response(self._state, data)


class TlsServer(Server):
    """
    TLS ClientHello server transport for Bob.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        self._config = config
        validated = validate_tls_config(config, 'server')

        self._pending_timeout = validated['pending_timeout']
        self._handshake_timeout = validated['handshake_timeout']
        self._max_record_recv = validated['max_clienthello_bytes']
        self._max_record_send = validated['max_serverhello_bytes']
        self._recv_packet_mtu = validated['client_payload_cap']
        self._send_packet_mtu = validated['server_payload_cap']
        self._max_in_flight = config.max_in_flight
        self._alpn_list = validated['alpn_list']
        self._clienthello_padding_target = validated['clienthello_padding_target']

        listen_host, listen_port = parse_host_port(config.tls_listen_addr)
        self._listen_addr = (listen_host, listen_port)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(self._listen_addr)
            self._sock.listen(5)
            self._sock.setblocking(False)
        except (socket.error, OSError) as exc:
            self._sock.close()
            self._sock = None
            raise_bind_error(exc, self._listen_addr, 'TLS')

        log_event(
            _LOG,
            logging.INFO,
            'tls.server_config',
            'TLS server config',
            lambda: {
                'listen_addr': '%s:%d' % (self._listen_addr[0], self._listen_addr[1]),
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'handshake_timeout': self._handshake_timeout,
                'max_clienthello_bytes': self._max_record_recv,
                'max_serverhello_bytes': self._max_record_send,
                'recv_packet_mtu': self._recv_packet_mtu,
                'send_packet_mtu': self._send_packet_mtu,
                'alpn': self._alpn_list,
                'clienthello_padding_target': self._clienthello_padding_target,
            },
        )

        self._active = {}

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    def recv(self, timeout=None):
        if self._sock is None:
            return (None, None)

        deadline = None
        if timeout is not None and timeout > 0:
            deadline = time_provider.now() + timeout

        while True:
            now = time_provider.now()
            self._prune_stale(now)
            read_list, write_list = self._build_select_lists()
            wait = self._select_timeout(now, deadline, timeout)
            try:
                ready_r, ready_w, _ = select.select(read_list, write_list, [], wait)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)

            for sock in ready_w:
                self._flush_response(sock)

            for sock in ready_r:
                if sock is self._sock:
                    self._accept_ready(now)
                else:
                    result = self._read_request(sock)
                    if result is not None:
                        return result

            if timeout == 0:
                return (None, None)
            if deadline is not None and time_provider.now() >= deadline:
                return (None, None)

    def _accept_ready(self, now):
        while True:
            try:
                client_sock, _addr = self._sock.accept()
            except socket.error as e:
                if _get_errno(e) in TEMP_ERRORS:
                    return
                raise TransportError('Accept failed: %s' % e)
            client_sock.setblocking(False)
            if len(self._active) >= self._max_in_flight:
                try:
                    client_sock.close()
                except Exception:
                    pass
                continue
            state = _ConnState(
                sock=client_sock,
                handshake_deadline=now + self._handshake_timeout,
            )
            self._active[client_sock] = state

    def _read_request(self, sock):
        state = self._active.get(sock)
        if state is None or state.ready_to_respond:
            return None
        bufsize = 4096
        if state.record_len is not None:
            expected = codec.TLS_RECORD_HEADER_LEN + state.record_len
            remaining = expected - len(state.recv_buf)
            if remaining <= 0:
                remaining = 1
            bufsize = min(bufsize, remaining + 1)
        try:
            data = sock.recv(bufsize)
        except socket.error as e:
            if _get_errno(e) in TEMP_ERRORS:
                return None
            self._close_conn(sock)
            raise TransportError('Receive failed: %s' % e)
        if not data:
            self._log_parse_error('tls.eof')
            self._close_conn(sock)
            return None
        state.recv_buf.extend(data)
        if state.record_len is None and len(state.recv_buf) >= codec.TLS_RECORD_HEADER_LEN:
            try:
                state.record_len = codec.parse_record_header(
                    state.recv_buf[:codec.TLS_RECORD_HEADER_LEN],
                    max_record_bytes=self._max_record_recv,
                )
            except ValueError:
                self._log_parse_error('tls.header')
                self._close_conn(sock)
                return None
        if state.record_len is None:
            return None
        expected = codec.TLS_RECORD_HEADER_LEN + state.record_len
        if len(state.recv_buf) > expected:
            self._log_parse_error('tls.extra')
            self._close_conn(sock)
            return None
        if len(state.recv_buf) < expected:
            return None
        try:
            payload, cipher_suites = codec.parse_client_hello_record(
                to_bytes(state.recv_buf),
                max_record_bytes=self._max_record_recv,
            )
        except ValueError:
            self._log_parse_error('tls.parse')
            self._close_conn(sock)
            return None
        if len(payload) > self._recv_packet_mtu:
            self._log_parse_error('tls.mtu')
            self._close_conn(sock)
            return None
        cipher_suite = self._select_cipher(cipher_suites)
        if cipher_suite is None:
            self._log_parse_error('tls.cipher')
            self._close_conn(sock)
            return None
        state.ready_to_respond = True
        state.cipher_suite = cipher_suite
        responder = _ResponseSender(self, state)
        log_event(
            _LOG,
            logging.DEBUG,
            'tls.recv',
            'TLS ClientHello received',
            lambda: {'payload_bytes': len(payload)},
        )
        return payload, responder

    def _enqueue_response(self, state, data):
        if state.sock is None or state.sock not in self._active:
            raise TransportError('Connection no longer active')
        if state.responder_used:
            raise TransportError('Responder already used')
        require_bytes_like(data)
        data = to_bytes(data)
        if len(data) > self._send_packet_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (
                    len(data), self._send_packet_mtu
                )
            )
        if state.cipher_suite is None:
            raise TransportError('No cipher suite selected')
        try:
            record = codec.build_server_hello_record(
                data,
                state.cipher_suite,
                include_payload=True,
                alpn_list=self._alpn_list,
            )
        except ValueError as exc:
            raise TransportError('TLS encode failed: %s' % exc)
        if len(record) > self._max_record_send:
            raise TransportError('TLS record exceeds configured max')
        state.response_buf = record
        state.response_off = 0
        state.responder_used = True
        log_event(
            _LOG,
            logging.DEBUG,
            'tls.send',
            'TLS ServerHello queued',
            lambda: {
                'record_bytes': len(record),
                'payload_bytes': len(data),
            },
        )

    def _flush_response(self, sock):
        state = self._active.get(sock)
        if state is None or state.response_buf is None:
            return
        if state.response_off >= len(state.response_buf):
            self._close_conn(sock)
            return
        try:
            sent = sock.send(state.response_buf[state.response_off:])
        except socket.error as e:
            if _get_errno(e) in TEMP_ERRORS:
                return
            self._close_conn(sock)
            raise TransportError('Send failed: %s' % e)
        if sent <= 0:
            self._close_conn(sock)
            raise TransportError('Send failed: connection closed')
        state.response_off += sent
        if state.response_off >= len(state.response_buf):
            self._close_conn(sock)

    def _select_cipher(self, cipher_suites):
        for cipher in cipher_suites:
            if cipher in codec.DEFAULT_CIPHER_SUITES:
                return cipher
        return None

    def _prune_stale(self, now):
        stale = []
        for sock, state in list(self._active.items()):
            if state.handshake_deadline is not None and now > state.handshake_deadline:
                stale.append(sock)
        for sock in stale:
            self._close_conn(sock)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'tls.prune_stale',
                'Pruned stale TLS connections',
                lambda: {'count': len(stale)},
            )

    def _build_select_lists(self):
        read_list = [self._sock]
        write_list = []
        for state in self._active.values():
            if state.response_buf is not None:
                write_list.append(state.sock)
            elif not state.ready_to_respond:
                read_list.append(state.sock)
        return read_list, write_list

    def _select_timeout(self, now, deadline, timeout):
        earliest = None
        for state in self._active.values():
            if state.handshake_deadline is not None:
                if earliest is None or state.handshake_deadline < earliest:
                    earliest = state.handshake_deadline
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

    def _close_conn(self, sock):
        state = self._active.pop(sock, None)
        if state is None:
            return
        try:
            sock.close()
        except Exception:
            pass
        state.sock = None

    def _log_parse_error(self, reason):
        log_event(
            _LOG,
            logging.DEBUG,
            'tls.parse_error',
            'TLS parse error',
            lambda: {'reason': reason},
        )

    def close(self):
        for sock in list(self._active.keys()):
            self._close_conn(sock)
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
