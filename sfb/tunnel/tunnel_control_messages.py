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


def tun_window_ok(size, final=False):
    """
    Window size negotiation response.

    Args:
        size: Agreed max in-flight packets
        final: True if the window cannot be increased further
    """
    if final:
        return ControlMessage(T_TUNNEL, 'window_ok', size=size, final=True)
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


def ch_close_err(ch, code, reason):
    """
    Channel closed with error.

    Args:
        ch: Channel ID
        code: Error code string
        reason: Error message
    """
    return ControlMessage(T_CHANNEL, 'close_err', ch=ch, code=code, reason=reason)


# =============================================================================
# Module Loader Messages (t="mod")
# =============================================================================

T_MOD = 'mod'


def mod_load(name, module_id):
    """Request to load a module by name and instance id."""
    return ControlMessage(T_MOD, 'load', name=name, mid=module_id)


def mod_load_ok(name, module_id):
    """Success response after loading a module."""
    return ControlMessage(T_MOD, 'load_ok', name=name, mid=module_id)


def mod_load_err(name, reason, module_id):
    """Error response when module loading fails."""
    return ControlMessage(T_MOD, 'load_err', name=name, reason=reason, mid=module_id)


def mod_unload(name, module_id):
    """Request to unload a module by name and instance id."""
    return ControlMessage(T_MOD, 'unload', name=name, mid=module_id)


def mod_unload_ok(name, module_id):
    """Success response after unloading a module."""
    return ControlMessage(T_MOD, 'unload_ok', name=name, mid=module_id)


def mod_unload_err(name, module_id, reason):
    """Error response when module unloading fails."""
    return ControlMessage(
        T_MOD,
        'unload_err',
        name=name,
        mid=module_id,
        reason=reason,
    )
