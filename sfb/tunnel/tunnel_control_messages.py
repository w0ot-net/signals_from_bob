# -*- coding: ascii -*-
"""
Tunnel control message helpers.

Defines the ControlMessage base class and message factories used by the
tunnel and channel layers.
"""

from __future__ import absolute_import

from ..control_message import ControlMessage, encode, validate


# Reserved message types
T_TUNNEL = 'tun'
T_CHANNEL = 'ch'

# =============================================================================
# Tunnel Messages (t="tun")
# =============================================================================

def tun_ping():
    """Keepalive request from Alice."""
    return ControlMessage(T_TUNNEL, 'ping')


def tun_pong():
    """Keepalive response from Bob."""
    return ControlMessage(T_TUNNEL, 'pong')


def tun_mtu(size):
    """
    MTU negotiation request.

    Args:
        size: Proposed maximum packet size in bytes
    """
    return ControlMessage(T_TUNNEL, 'mtu', size=size)


def tun_mtu_ok(size):
    """
    MTU negotiation response.

    Args:
        size: Agreed maximum packet size in bytes
    """
    return ControlMessage(T_TUNNEL, 'mtu_ok', size=size)


def tun_mtu_ack():
    """
    MTU negotiation acknowledgment.

    Sent by Alice after receiving mtu_ok to confirm both sides
    can now use the new MTU.
    """
    return ControlMessage(T_TUNNEL, 'mtu_ack')


def tun_window(size):
    """
    Window size negotiation request.

    Args:
        size: Proposed max in-flight packets
    """
    return ControlMessage(T_TUNNEL, 'window', size=size)


def tun_window_ok(size):
    """
    Window size negotiation response.

    Args:
        size: Agreed max in-flight packets
    """
    return ControlMessage(T_TUNNEL, 'window_ok', size=size)


# =============================================================================
# Channel Messages (t="ch")
# =============================================================================

def ch_open(ch):
    """
    Request to open a channel.

    Channels are generic bidirectional byte streams. Application-specific
    data (like connection targets) should be negotiated separately after
    the channel is open.

    Args:
        ch: Channel ID (odd=Alice, even=Bob)
    """
    return ControlMessage(T_CHANNEL, 'open', ch=ch)


def ch_open_ok(ch):
    """
    Channel opened successfully.

    Args:
        ch: Channel ID
    """
    return ControlMessage(T_CHANNEL, 'open_ok', ch=ch)


def ch_open_fail(ch, reason):
    """
    Channel open failed.

    Args:
        ch: Channel ID
        reason: Failure reason string
    """
    return ControlMessage(T_CHANNEL, 'open_fail', ch=ch, reason=reason)


def ch_close(ch):
    """
    Request to close a channel.

    Args:
        ch: Channel ID
    """
    return ControlMessage(T_CHANNEL, 'close', ch=ch)


def ch_close_ok(ch):
    """
    Channel closed.

    Args:
        ch: Channel ID
    """
    return ControlMessage(T_CHANNEL, 'close_ok', ch=ch)
