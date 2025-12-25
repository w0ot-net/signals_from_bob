# -*- coding: ascii -*-
"""
DNS transport for tunnel protocol.
"""

from __future__ import absolute_import

from .codec import (
    QTYPE_TXT,
    QTYPE_NULL,
)
from .dns_client import DnsClient
from .dns_server import DnsServer

__all__ = [
    'DnsClient',
    'DnsServer',
    'QTYPE_TXT',
    'QTYPE_NULL',
]
