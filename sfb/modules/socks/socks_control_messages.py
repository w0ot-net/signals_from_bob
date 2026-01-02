# -*- coding: ascii -*-
"""
SOCKS proxy control message helpers.
"""

from __future__ import absolute_import

from ...control_message import ControlMessage

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
    return ControlMessage(T_SOCK, 'connect', rid=rid, ch=ch, host=host, port=port)


def sock_connect_ok(rid, ch, bhost=None, bport=None):
    """
    Connection successful.

    Args:
        rid: Request ID
        ch: Channel ID
        bhost: Bound address (for SOCKS5 reply)
        bport: Bound port (for SOCKS5 reply)
    """
    fields = {'rid': rid, 'ch': ch}
    if bhost is not None:
        fields['bhost'] = bhost
    if bport is not None:
        fields['bport'] = bport
    return ControlMessage(T_SOCK, 'connect_ok', **fields)


def sock_err(rid, ch, code, reason):
    """
    Connection or relay error.

    Args:
        rid: Request ID
        ch: Channel ID
        code: Error code (refused, timeout, unreachable_host, unreachable_net, general)
        reason: Human-readable reason
    """
    return ControlMessage(T_SOCK, 'err', rid=rid, ch=ch, code=code, reason=reason)
