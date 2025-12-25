# -*- coding: ascii -*-
"""
DNS transport for tunnel protocol.
"""

from __future__ import absolute_import

from .dns_client import (
    DnsClient,
    QTYPE_TXT,
    QTYPE_NULL,
)

__all__ = [
    'DnsClient',
    'QTYPE_TXT',
    'QTYPE_NULL',
]
