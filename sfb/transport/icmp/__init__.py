# -*- coding: ascii -*-
"""
ICMP transport for tunnel protocol.
"""

from __future__ import absolute_import

from .icmp_client import IcmpClient
try:
    from .icmp_server import IcmpServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in ('sfb.transport.icmp.icmp_server', 'icmp_server'):
        _msg = str(exc)
        if ("No module named 'sfb.transport.icmp.icmp_server'" not in _msg and
                "No module named 'icmp_server'" not in _msg and
                "No module named sfb.transport.icmp.icmp_server" not in _msg and
                "No module named icmp_server" not in _msg):
            raise
from .icmp_packet import ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY

__all__ = [
    'IcmpClient',
    'ICMP_ECHO_REQUEST',
    'ICMP_ECHO_REPLY',
]
if 'IcmpServer' in globals():
    __all__.insert(1, 'IcmpServer')
