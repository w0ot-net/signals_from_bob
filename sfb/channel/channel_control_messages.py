# -*- coding: ascii -*-
"""
Channel control message helpers.
"""

from __future__ import absolute_import

from ..control_message import ControlMessage, encode, validate

T_CHANNEL = 'ch'


def ch_open(ch):
    """
    Request to open a channel.

    Channels are generic bidirectional byte streams. Application-specific
    data (like connection targets) should be negotiated separately after
    the channel is open.
    """
    return ControlMessage(T_CHANNEL, 'open', ch=ch)


def ch_open_ok(ch):
    """Channel opened successfully."""
    return ControlMessage(T_CHANNEL, 'open_ok', ch=ch)


def ch_open_fail(ch, reason):
    """Channel open failed."""
    return ControlMessage(T_CHANNEL, 'open_fail', ch=ch, reason=reason)


def ch_close(ch):
    """Request to close a channel."""
    return ControlMessage(T_CHANNEL, 'close', ch=ch)


def ch_close_ok(ch):
    """Channel closed."""
    return ControlMessage(T_CHANNEL, 'close_ok', ch=ch)


def ch_close_err(ch, code, reason):
    """Channel closed with error."""
    return ControlMessage(T_CHANNEL, 'close_err', ch=ch, code=code, reason=reason)


def ch_half_close(ch):
    """Sender will not send more data on this channel."""
    return ControlMessage(T_CHANNEL, 'half_close', ch=ch)
