# -*- coding: ascii -*-
"""
TLS handshake bump transport package.
"""

from __future__ import absolute_import

from . import tls_handshake_bump_codec as codec
from .tls_handshake_bump_client import TlsHandshakeBumpClient
try:
    from .tls_handshake_bump_server import TlsHandshakeBumpServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in (
            'sfb.transport.tls_handshake_bump.tls_handshake_bump_server',
            'tls_handshake_bump_server',
    ):
        _msg = str(exc)
        if ("No module named 'sfb.transport.tls_handshake_bump.tls_handshake_bump_server'" not in _msg and
                "No module named 'tls_handshake_bump_server'" not in _msg and
                "No module named sfb.transport.tls_handshake_bump.tls_handshake_bump_server" not in _msg and
                "No module named tls_handshake_bump_server" not in _msg):
            raise

__all__ = [
    'TlsHandshakeBumpClient',
    'codec',
]
if 'TlsHandshakeBumpServer' in globals():
    __all__.insert(1, 'TlsHandshakeBumpServer')
