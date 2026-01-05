# -*- coding: ascii -*-
"""
Shared relay control message helpers.
"""

from __future__ import absolute_import

from ..control_message import ControlMessage


def relay_connect(msg_type, rid, ch, host, port):
    """
    Request to connect to target host.

    Args:
        msg_type: Message type (e.g., 'sock', 'fwd')
        rid: Request ID for correlation
        ch: Channel ID (opened by server)
        host: Target hostname or IP
        port: Target port
    """
    return ControlMessage(msg_type, 'connect', rid=rid, ch=ch, host=host, port=port)


def relay_connect_ok(msg_type, rid, ch, extra=None):
    """
    Connection successful.

    Args:
        msg_type: Message type (e.g., 'sock', 'fwd')
        rid: Request ID
        ch: Channel ID
        extra: Optional dict of additional fields
    """
    fields = {'rid': rid, 'ch': ch}
    if extra:
        fields.update(extra)
    return ControlMessage(msg_type, 'connect_ok', **fields)


def relay_err(msg_type, rid, ch, code, reason):
    """
    Connection or relay error.

    Args:
        msg_type: Message type (e.g., 'sock', 'fwd')
        rid: Request ID
        ch: Channel ID
        code: Error code (refused, timeout, unreachable_host, unreachable_net, general)
        reason: Human-readable reason
    """
    return ControlMessage(msg_type, 'err', rid=rid, ch=ch, code=code, reason=reason)
