# -*- coding: ascii -*-
"""
Shared DNS constants for parsing offsets.
"""

from __future__ import absolute_import

DNS_HEADER_LEN = 12
DNS_QUESTION_FIXED_LEN = 4  # QTYPE + QCLASS
DNS_RR_FIXED_LEN = 10  # TYPE + CLASS + TTL + RDLENGTH
