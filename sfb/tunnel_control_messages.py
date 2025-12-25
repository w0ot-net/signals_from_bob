# -*- coding: ascii -*-
"""
Core control message factories for tunnel and channel layers.

Messages use the format: {"t": "<type>", "c": "<command>", ...}

This module defines factories for reserved message types:
- tun: Tunnel-level messages (ping, pong, mtu, window)
- ch: Channel-level messages (open, close)

Module-specific messages (file, sh, sock) are defined in their
respective modules, not here.
"""

from __future__ import absolute_import

# Reserved message types
T_TUNNEL = 'tun'
T_CHANNEL = 'ch'

# =============================================================================
# Tunnel Messages (t="tun")
# =============================================================================

def tun_ping():
    """Keepalive request from Alice."""
    return {'t': T_TUNNEL, 'c': 'ping'}


def tun_pong():
    """Keepalive response from Bob."""
    return {'t': T_TUNNEL, 'c': 'pong'}


def tun_mtu(size):
    """
    MTU negotiation request.

    Args:
        size: Proposed maximum packet size in bytes
    """
    return {'t': T_TUNNEL, 'c': 'mtu', 'size': size}


def tun_mtu_ok(size):
    """
    MTU negotiation response.

    Args:
        size: Agreed maximum packet size in bytes
    """
    return {'t': T_TUNNEL, 'c': 'mtu_ok', 'size': size}


def tun_window(size):
    """
    Window size negotiation request.

    Args:
        size: Proposed max in-flight packets
    """
    return {'t': T_TUNNEL, 'c': 'window', 'size': size}


def tun_window_ok(size):
    """
    Window size negotiation response.

    Args:
        size: Agreed max in-flight packets
    """
    return {'t': T_TUNNEL, 'c': 'window_ok', 'size': size}


# =============================================================================
# Channel Messages (t="ch")
# =============================================================================

def ch_open(ch, atype, addr, port):
    """
    Request to open a channel.

    Args:
        ch: Channel ID (odd=Alice, even=Bob)
        atype: Address type ('ipv4', 'ipv6', 'domain')
        addr: Target address
        port: Target port
    """
    return {
        't': T_CHANNEL,
        'c': 'open',
        'ch': ch,
        'atype': atype,
        'addr': addr,
        'port': port,
    }


def ch_open_ok(ch):
    """
    Channel opened successfully.

    Args:
        ch: Channel ID
    """
    return {'t': T_CHANNEL, 'c': 'open_ok', 'ch': ch}


def ch_open_fail(ch, reason):
    """
    Channel open failed.

    Args:
        ch: Channel ID
        reason: Failure reason string
    """
    return {'t': T_CHANNEL, 'c': 'open_fail', 'ch': ch, 'reason': reason}


def ch_close(ch):
    """
    Request to close a channel.

    Args:
        ch: Channel ID
    """
    return {'t': T_CHANNEL, 'c': 'close', 'ch': ch}


def ch_close_ok(ch):
    """
    Channel closed.

    Args:
        ch: Channel ID
    """
    return {'t': T_CHANNEL, 'c': 'close_ok', 'ch': ch}


# =============================================================================
# Serialization Helpers
# =============================================================================

def encode(msg):
    """
    Encode a message dict to bytes for transmission.

    Args:
        msg: Message dict

    Returns:
        bytes: JSON-encoded message with newline terminator
    """
    import json
    line = json.dumps(msg, separators=(',', ':'), ensure_ascii=True)
    return line.encode('ascii') + b'\n'
