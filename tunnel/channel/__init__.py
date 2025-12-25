# -*- coding: ascii -*-
"""
Channel layer for tunnel protocol.

Channels are logical TCP-like streams multiplexed over the tunnel.
"""

from __future__ import absolute_import

from .channel import (
    Channel,
    ChannelError,
    CHANNEL_CONTROL,
    STATE_INIT,
    STATE_OPENING,
    STATE_OPEN,
    STATE_CLOSING,
    STATE_CLOSED,
    is_alice_channel,
    is_bob_channel,
)
from .control_channel import ControlChannel
from .channel_manager import ChannelManager

__all__ = [
    'Channel',
    'ControlChannel',
    'ChannelManager',
    'ChannelError',
    'CHANNEL_CONTROL',
    'STATE_INIT',
    'STATE_OPENING',
    'STATE_OPEN',
    'STATE_CLOSING',
    'STATE_CLOSED',
    'is_alice_channel',
    'is_bob_channel',
]
