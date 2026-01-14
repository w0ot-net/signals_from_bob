# -*- coding: ascii -*-
"""
DNS TXT transport for tunnel protocol.
"""

from __future__ import absolute_import

from . import dns_txt_codec as codec
from .dns_txt_codec import QTYPE_TXT
from .dns_txt_client import DnsTxtClient
try:
    from .dns_txt_server import DnsTxtServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in ('sfb.transport.dns_txt.dns_txt_server', 'dns_txt_server'):
        _msg = str(exc)
        if ("No module named 'sfb.transport.dns_txt.dns_txt_server'" not in _msg and
                "No module named 'dns_txt_server'" not in _msg and
                "No module named sfb.transport.dns_txt.dns_txt_server" not in _msg and
                "No module named dns_txt_server" not in _msg):
            raise

__all__ = [
    'DnsTxtClient',
    'QTYPE_TXT',
]
if 'DnsTxtServer' in globals():
    __all__.insert(1, 'DnsTxtServer')
