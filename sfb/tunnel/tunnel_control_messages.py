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
    """Legacy keepalive request (not emitted; ignored if received)."""
    return ControlMessage(T_TUNNEL, 'ping')


def tun_pong():
    """Legacy keepalive response (not emitted; ignored if received)."""
    return ControlMessage(T_TUNNEL, 'pong')


def tun_mtu(tx, rx):
    """
    MTU negotiation request (asymmetric).

    Args:
        tx: Sender->receiver payload MTU in bytes
        rx: Receiver->sender payload MTU in bytes
    """
    return ControlMessage(T_TUNNEL, 'mtu', tx=tx, rx=rx)


def tun_mtu_ok(tx, rx):
    """
    MTU negotiation response (asymmetric).

    Args:
        tx: Agreed sender->receiver payload MTU
        rx: Agreed receiver->sender payload MTU
    """
    return ControlMessage(T_TUNNEL, 'mtu_ok', tx=tx, rx=rx)


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


# =============================================================================
# Module Loader Messages (t="mod")
# =============================================================================

T_MOD = 'mod'


def mod_load(name):
    """Request to load a module by name."""
    return ControlMessage(T_MOD, 'load', name=name)


def mod_load_ok(name):
    """Success response after loading a module."""
    return ControlMessage(T_MOD, 'load_ok', name=name)


def mod_load_err(name, reason):
    """Error response when module loading fails."""
    return ControlMessage(T_MOD, 'load_err', name=name, reason=reason)
