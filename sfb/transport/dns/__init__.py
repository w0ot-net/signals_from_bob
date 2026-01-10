# -*- coding: ascii -*-
"""
DNS transport for tunnel protocol.
"""

from __future__ import absolute_import

from . import dns_codec as codec
from .dns_codec import (
    QTYPE_A,
    QTYPE_AAAA,
    QTYPE_CNAME,
    QTYPE_TXT,
    QTYPE_NULL,
    RECORD_TYPES,
)
from .dns_client import DnsClient
try:
    from .dns_server import DnsServer
except ImportError as exc:
    _name = getattr(exc, 'name', None)
    if _name not in ('sfb.transport.dns.dns_server', 'dns_server'):
        _msg = str(exc)
        if ("No module named 'sfb.transport.dns.dns_server'" not in _msg and
                "No module named 'dns_server'" not in _msg and
                "No module named sfb.transport.dns.dns_server" not in _msg and
                "No module named dns_server" not in _msg):
            raise

__all__ = [
    'DnsClient',
    'QTYPE_A',
    'QTYPE_AAAA',
    'QTYPE_CNAME',
    'QTYPE_TXT',
    'QTYPE_NULL',
    'RECORD_TYPES',
]
if 'DnsServer' in globals():
    __all__.insert(1, 'DnsServer')
