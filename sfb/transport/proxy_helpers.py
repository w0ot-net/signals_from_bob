# -*- coding: ascii -*-
"""
Shared HTTP CONNECT proxy helpers.
"""

from __future__ import absolute_import

import base64

from .transport_base import TransportError
from ..compat import text_type
from ..utils import build_host_port_error_map, parse_host_port_or_raise


PROXY_HEADER_LIMIT = 8192
PROXY_HEADER_TOO_LARGE = -1


_HOST_PORT_ERROR_MAP = build_host_port_error_map(TransportError)


def build_connect_request(target_hostport, proxy_auth=None):
    """
    Build a CONNECT request for the target host:port.
    """
    try:
        target_bytes = target_hostport.encode('ascii')
    except UnicodeError:
        raise TransportError('tls_target must be ASCII when using tls_http_proxy')
    lines = [
        b'CONNECT ' + target_bytes + b' HTTP/1.1',
        b'Host: ' + target_bytes,
    ]
    if proxy_auth is not None:
        try:
            auth_bytes = proxy_auth.encode('ascii')
        except UnicodeError:
            raise TransportError('tls_http_proxy_auth must be ASCII')
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


def validate_proxy_config(tls_http_proxy, tls_http_proxy_auth,
                          tls_proxy_timeout, connect_timeout):
    """
    Validate proxy config inputs and return normalized values.
    """
    proxy_timeout = None
    proxy_addr = None
    proxy_auth = None
    if tls_http_proxy is not None:
        proxy_addr = _validate_proxy_addr(tls_http_proxy)
        if tls_http_proxy_auth is not None:
            proxy_auth = _validate_proxy_auth(tls_http_proxy_auth)
        if tls_proxy_timeout is None:
            proxy_timeout = connect_timeout
        else:
            proxy_timeout = _require_positive_float(
                tls_proxy_timeout, 'tls_proxy_timeout'
            )
    else:
        if tls_http_proxy_auth is not None:
            raise TransportError('tls_http_proxy_auth requires tls_http_proxy')
        if tls_proxy_timeout is not None:
            proxy_timeout = _require_positive_float(
                tls_proxy_timeout, 'tls_proxy_timeout'
            )
    return {
        'tls_http_proxy': proxy_addr,
        'tls_http_proxy_auth': proxy_auth,
        'proxy_timeout': proxy_timeout,
    }


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


def _validate_proxy_addr(value):
    value = _require_ascii_text(value, 'tls_http_proxy')
    if any(ch.isspace() for ch in value):
        raise TransportError('tls_http_proxy must not contain whitespace')
    parse_host_port_or_raise(value, _HOST_PORT_ERROR_MAP)
    return value


def _validate_proxy_auth(value):
    value = _require_ascii_text(value, 'tls_http_proxy_auth')
    if ':' not in value:
        raise TransportError('tls_http_proxy_auth must be user:pass')
    return value


def _require_positive_float(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be a number' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value
