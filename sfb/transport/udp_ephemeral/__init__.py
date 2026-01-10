# -*- coding: ascii -*-
"""
UDP ephemeral transport for tunnel protocol.
"""

from __future__ import absolute_import

from .udp_ephemeral_client import UdpEphemeralClient
try:
    from .udp_ephemeral_server import UdpEphemeralServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in (
            'sfb.transport.udp_ephemeral.udp_ephemeral_server',
            'udp_ephemeral_server',
    ):
        _msg = str(exc)
        if ("No module named 'sfb.transport.udp_ephemeral.udp_ephemeral_server'" not in _msg and
                "No module named 'udp_ephemeral_server'" not in _msg and
                "No module named sfb.transport.udp_ephemeral.udp_ephemeral_server" not in _msg and
                "No module named udp_ephemeral_server" not in _msg):
            raise

__all__ = [
    'UdpEphemeralClient',
]
if 'UdpEphemeralServer' in globals():
    __all__.append('UdpEphemeralServer')
