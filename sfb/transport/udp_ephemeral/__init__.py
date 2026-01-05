# -*- coding: ascii -*-
"""
UDP ephemeral transport for tunnel protocol.
"""

from __future__ import absolute_import

from .udp_ephemeral_client import UdpEphemeralClient
from .udp_ephemeral_server import UdpEphemeralServer

__all__ = [
    'UdpEphemeralClient',
    'UdpEphemeralServer',
]
