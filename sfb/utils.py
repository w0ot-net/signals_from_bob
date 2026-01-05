# -*- coding: ascii -*-
"""
Shared helpers for sfb.
"""

from __future__ import absolute_import

from .compat import text_type


def parse_host_port(addr, default_port=None):
    """
    Parse a host:port string with optional default port.

    Args:
        addr: host:port string
        default_port: int or None for required port

    Returns:
        tuple: (host, port)

    Raises:
        ValueError: if addr is invalid or unsupported
    """
    if not isinstance(addr, text_type):
        raise ValueError('Address must be text')
    try:
        addr.encode('ascii')
    except UnicodeError:
        raise ValueError('Address must be ASCII')

    if addr.startswith('['):
        raise ValueError('Address must be host:port (IPv6 unsupported)')

    port_default = None
    if default_port is not None:
        try:
            port_default = int(default_port)
        except (TypeError, ValueError):
            raise ValueError('Default port invalid')
        if port_default < 1 or port_default > 65535:
            raise ValueError('Default port out of range')

    colon_count = addr.count(':')
    if colon_count == 0:
        if port_default is None:
            raise ValueError('Address must include port')
        host = addr
        port = port_default
    elif colon_count == 1:
        host, port_text = addr.rsplit(':', 1)
        if not host:
            raise ValueError('Address host required')
        try:
            port = int(port_text, 10)
        except ValueError:
            raise ValueError('Address port invalid')
    else:
        raise ValueError('Address must be host:port (IPv6 unsupported)')

    if not host:
        raise ValueError('Address host required')
    if port < 1 or port > 65535:
        raise ValueError('Address port out of range')
    return host, port
