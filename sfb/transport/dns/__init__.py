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
from .dns_server import DnsServer

__all__ = [
    'DnsClient',
    'DnsServer',
    'QTYPE_A',
    'QTYPE_AAAA',
    'QTYPE_CNAME',
    'QTYPE_TXT',
    'QTYPE_NULL',
    'RECORD_TYPES',
]
