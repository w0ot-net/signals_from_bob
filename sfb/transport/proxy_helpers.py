# -*- coding: ascii -*-
"""
Shared HTTP CONNECT proxy helpers.
"""

from __future__ import absolute_import

import base64
import socket

from .transport_base import TransportError
from ..compat import buffer_view, text_type
from ..utils import build_host_port_error_map, parse_host_port_or_raise


PROXY_HEADER_LIMIT = 8192
PROXY_HEADER_TOO_LARGE = -1
PROXY_IN_PROGRESS = 'in_progress'
PROXY_DONE = 'done'
PROXY_CLOSED = 'closed'


_HOST_PORT_ERROR_MAP = build_host_port_error_map(TransportError)


def build_connect_request(target_hostport, proxy_auth=None,
                          target_label='tls_target',
                          proxy_label='tls_http_proxy',
                          proxy_auth_label='tls_http_proxy_auth'):
    """
    Build a CONNECT request for the target host:port.
    """
    try:
        target_bytes = target_hostport.encode('ascii')
    except UnicodeError:
        raise TransportError(
            '%s must be ASCII when using %s' % (target_label, proxy_label)
        )
    lines = [
        b'CONNECT ' + target_bytes + b' HTTP/1.1',
        b'Host: ' + target_bytes,
    ]
    if proxy_auth is not None:
        try:
            auth_bytes = proxy_auth.encode('ascii')
        except UnicodeError:
            raise TransportError('%s must be ASCII' % proxy_auth_label)
        token = base64.b64encode(auth_bytes)
        lines.append(b'Proxy-Authorization: Basic ' + token)
    lines.append(b'')
    lines.append(b'')
    return b'\r\n'.join(lines)


def parse_connect_response(buffer, start_offset=0):
    """
    Parse a CONNECT response buffer.

    start_offset: optional index to begin scanning for header terminator.

    Returns:
        tuple: (status, header_end)
            status: int when parsed, None if invalid or incomplete.
            header_end: int index for header terminator, None if incomplete,
                PROXY_HEADER_TOO_LARGE if buffer exceeds limit.
    """
    if len(buffer) > PROXY_HEADER_LIMIT:
        return None, PROXY_HEADER_TOO_LARGE
    if start_offset is None or start_offset < 0:
        start_offset = 0
    if start_offset > len(buffer):
        start_offset = len(buffer)
    header_end = buffer.find(b'\r\n\r\n', start_offset)
    if header_end < 0:
        return None, None
    status_line = buffer[:header_end].split(b'\r\n', 1)[0]
    status = _parse_status_line(status_line)
    return status, header_end


def validate_proxy_config(proxy, proxy_auth, proxy_timeout, connect_timeout,
                          proxy_label='tls_http_proxy',
                          proxy_auth_label='tls_http_proxy_auth',
                          proxy_timeout_label='tls_proxy_timeout',
                          host_port_error_map=None):
    """
    Validate proxy config inputs and return normalized values.
    """
    if host_port_error_map is None:
        host_port_error_map = _HOST_PORT_ERROR_MAP
    proxy_timeout_value = None
    proxy_addr = None
    proxy_auth_value = None
    if proxy is not None:
        proxy_addr = _validate_proxy_addr(
            proxy, proxy_label, host_port_error_map
        )
        if proxy_auth is not None:
            proxy_auth_value = _validate_proxy_auth(
                proxy_auth, proxy_auth_label
            )
        if proxy_timeout is None:
            proxy_timeout_value = connect_timeout
        else:
            proxy_timeout_value = _require_positive_float(
                proxy_timeout, proxy_timeout_label
            )
    else:
        if proxy_auth is not None:
            raise TransportError(
                '%s requires %s' % (proxy_auth_label, proxy_label)
            )
        if proxy_timeout is not None:
            proxy_timeout_value = _require_positive_float(
                proxy_timeout, proxy_timeout_label
            )
    return {
        'proxy_addr': proxy_addr,
        'proxy_auth': proxy_auth_value,
        'proxy_timeout': proxy_timeout_value,
    }


class ProxyConnect(object):
    """
    Drive a proxy CONNECT handshake with shared state.
    """

    __slots__ = (
        '_sock',
        '_send_buf',
        '_send_off',
        '_recv_buf',
        '_scan_offset',
        '_deadline',
        '_get_errno',
        '_temp_errors',
        '_log_cb',
    )

    def __init__(self, sock, send_buf, get_errno, temp_errors, log_cb):
        self._sock = sock
        self._send_buf = send_buf
        self._send_off = 0
        self._recv_buf = bytearray()
        self._scan_offset = 0
        self._deadline = None
        self._get_errno = get_errno
        self._temp_errors = temp_errors
        self._log_cb = log_cb

    def set_deadline(self, deadline):
        self._deadline = deadline

    def deadline(self):
        return self._deadline

    def wants_read(self):
        if self._sock is None:
            return False
        if self._send_buf is None:
            return True
        return self._send_off >= len(self._send_buf)

    def wants_write(self):
        if self._sock is None:
            return False
        if self._send_buf is None:
            return False
        return self._send_off < len(self._send_buf)

    def drive(self, can_read, can_write, now):
        if self._sock is None:
            return PROXY_CLOSED
        if can_write and self.wants_write():
            if not self._flush_send():
                return PROXY_IN_PROGRESS
            if not can_read:
                return PROXY_IN_PROGRESS
        if can_read and self.wants_read():
            return self._recv_response()
        return PROXY_IN_PROGRESS

    def _flush_send(self):
        if self._send_buf is None:
            return True
        if self._send_off >= len(self._send_buf):
            return True
        view = buffer_view(self._send_buf)
        try:
            sent = self._sock.send(view[self._send_off:])
        except socket.error as e:
            err = self._get_errno(e)
            if err in self._temp_errors:
                return False
            self._log_cb('send_error', error=err)
            raise TransportError('Proxy send failed: %s' % e)
        if sent <= 0:
            self._log_cb('send_error', error='closed')
            raise TransportError('Proxy send failed: connection closed')
        self._send_off += sent
        return self._send_off >= len(self._send_buf)

    def _recv_response(self):
        try:
            data = self._sock.recv(4096)
        except socket.error as e:
            err = self._get_errno(e)
            if err in self._temp_errors:
                return PROXY_IN_PROGRESS
            self._log_cb('recv_error', error=err)
            raise TransportError('Proxy receive failed: %s' % e)
        if not data:
            self._log_cb('eof')
            return self._finish(PROXY_CLOSED)
        self._recv_buf.extend(data)
        status, header_end = parse_connect_response(
            self._recv_buf,
            start_offset=self._scan_offset,
        )
        if header_end == PROXY_HEADER_TOO_LARGE:
            self._log_cb('response_too_large', bytes=len(self._recv_buf))
            return self._finish(PROXY_CLOSED)
        if header_end is None:
            self._scan_offset = max(0, len(self._recv_buf) - 3)
            return PROXY_IN_PROGRESS
        if status != 200:
            if status is None:
                self._log_cb('invalid_status')
            else:
                self._log_cb('bad_status', status=status)
            return self._finish(PROXY_CLOSED)
        extra = self._recv_buf[header_end + 4:]
        if extra and any(byte not in (13, 10) for byte in bytearray(extra)):
            self._log_cb('extra_bytes', bytes=len(extra))
            return self._finish(PROXY_CLOSED)
        return self._finish(PROXY_DONE)

    def _finish(self, status):
        self._sock = None
        self._send_buf = None
        self._recv_buf = bytearray()
        self._scan_offset = 0
        self._deadline = None
        return status


def _parse_status_line(status_line):
    parts = status_line.split(None, 2)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1], 10)
    except (TypeError, ValueError):
        return None


def _require_ascii_text(value, label):
    if not isinstance(value, text_type):
        raise TransportError('%s must be text' % label)
    if not value:
        raise TransportError('%s must not be empty' % label)
    try:
        value.encode('ascii')
    except UnicodeError:
        raise TransportError('%s must be ASCII' % label)
    return value


def _validate_proxy_addr(value, label, host_port_error_map):
    value = _require_ascii_text(value, label)
    if any(ch.isspace() for ch in value):
        raise TransportError('%s must not contain whitespace' % label)
    parse_host_port_or_raise(value, host_port_error_map)
    return value


def _validate_proxy_auth(value, label):
    value = _require_ascii_text(value, label)
    if ':' not in value:
        raise TransportError('%s must be user:pass' % label)
    return value


def _require_positive_float(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be a number' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value
