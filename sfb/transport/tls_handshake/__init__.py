# -*- coding: ascii -*-
"""
TLS ClientHello transport package.
"""

from __future__ import absolute_import

from . import tls_handshake_codec as codec
from .tls_handshake_client import TlsClient
try:
    from .tls_handshake_server import TlsServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in (
            'sfb.transport.tls_handshake.tls_handshake_server',
            'tls_handshake_server',
    ):
        _msg = str(exc)
        if ("No module named 'sfb.transport.tls_handshake.tls_handshake_server'" not in _msg and
                "No module named 'tls_handshake_server'" not in _msg and
                "No module named sfb.transport.tls_handshake.tls_handshake_server" not in _msg and
                "No module named tls_handshake_server" not in _msg):
            raise

__all__ = [
    'TlsClient',
    'codec',
]
if 'TlsServer' in globals():
    __all__.insert(1, 'TlsServer')
