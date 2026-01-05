# -*- coding: ascii -*-
"""
Shared utility helpers.
"""

from __future__ import absolute_import

from .compat import integer_types, text_type


class HostPortError(ValueError):
    """Host:port parsing error with a stable code."""

    def __init__(self, code, message):
        ValueError.__init__(self, message)
        self.code = code


HOST_PORT_ERROR_MESSAGES = {
    'not_text': 'Address must be text',
    'not_ascii': 'Address must be ASCII',
    'missing_host': 'Address host required',
    'missing_port': 'Address must include port',
    'invalid_port': 'Address port invalid',
    'port_range': 'Address port out of range',
    'ipv6_unsupported': 'Address must be host:port (IPv6 unsupported)',
}


def _require_ascii_text(value):
    if not isinstance(value, text_type):
        raise HostPortError('not_text', 'Address must be text')
    if not value:
        raise HostPortError('missing_host', 'Address host required')
    try:
        value.encode('ascii')
    except UnicodeError:
        raise HostPortError('not_ascii', 'Address must be ASCII')
    return value


def _normalize_default_port(default_port):
    if default_port is None:
        return None
    if not isinstance(default_port, integer_types):
        try:
            default_port = int(default_port, 10)
        except (TypeError, ValueError):
            raise HostPortError('invalid_port', 'Address port invalid')
    if default_port < 1 or default_port > 65535:
        raise HostPortError('port_range', 'Address port out of range')
    return default_port


def parse_host_port(addr, default_port=None):
    """
    Parse host:port input with optional default port.
    """
    addr = _require_ascii_text(addr)
    default_port = _normalize_default_port(default_port)
    if addr.startswith('[') or ']' in addr or addr.count(':') > 1:
        raise HostPortError('ipv6_unsupported', 'Address must be host:port (IPv6 unsupported)')
    if ':' not in addr:
        if default_port is None:
            raise HostPortError('missing_port', 'Address must include port')
        return addr, default_port
    host, port_text = addr.rsplit(':', 1)
    if not host:
        raise HostPortError('missing_host', 'Address host required')
    if not port_text:
        raise HostPortError('missing_port', 'Address must include port')
    try:
        port = int(port_text, 10)
    except (TypeError, ValueError):
        raise HostPortError('invalid_port', 'Address port invalid')
    if port < 1 or port > 65535:
        raise HostPortError('port_range', 'Address port out of range')
    return host, port


def build_host_port_error_map(error_type, base_message=None, overrides=None):
    """
    Build a HostPortError code map for parse_host_port_or_raise.
    """
    messages = dict(HOST_PORT_ERROR_MESSAGES)
    if base_message is not None:
        for code in ('not_text', 'not_ascii', 'missing_host', 'missing_port'):
            messages[code] = base_message
    if overrides:
        for code, message in overrides.items():
            messages[code] = message
    error_map = {}
    for code, message in messages.items():
        error_map[code] = (error_type, message)
    return error_map


def parse_host_port_or_raise(addr, error_map, default_port=None):
    """
    Parse host:port and map HostPortError to caller-supplied exceptions.
    """
    try:
        return parse_host_port(addr, default_port=default_port)
    except HostPortError as exc:
        mapped = error_map.get(exc.code)
        if mapped is None:
            raise
        err_type, message = mapped
        raise err_type(message)
