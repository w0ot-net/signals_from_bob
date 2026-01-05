# -*- coding: ascii -*-
"""
SOCKS proxy control message helpers.
"""

from __future__ import absolute_import

from ..relay_control_messages import (
    relay_connect,
    relay_connect_ok,
    relay_err,
)

T_SOCK = 'sock'


def sock_connect(rid, ch, host, port):
    """
    Request to connect to target host.

    Args:
        rid: Request ID for correlation
        ch: Channel ID (opened by server)
        host: Target hostname or IP
        port: Target port
    """
    return relay_connect(T_SOCK, rid, ch, host, port)


def sock_connect_ok(rid, ch, bhost=None, bport=None):
    """
    Connection successful.

    Args:
        rid: Request ID
        ch: Channel ID
        bhost: Bound address (for SOCKS5 reply)
        bport: Bound port (for SOCKS5 reply)
    """
    fields = {}
    if bhost is not None:
        fields['bhost'] = bhost
    if bport is not None:
        fields['bport'] = bport
    return relay_connect_ok(T_SOCK, rid, ch, extra=fields or None)


def sock_err(rid, ch, code, reason):
    """
    Connection or relay error.

    Args:
        rid: Request ID
        ch: Channel ID
        code: Error code (refused, timeout, unreachable_host, unreachable_net, general)
        reason: Human-readable reason
    """
    return relay_err(T_SOCK, rid, ch, code, reason)
