# -*- coding: ascii -*-
"""
ICMP packet helpers for Echo Request/Reply.
"""

from __future__ import absolute_import

import socket
import struct

from ...compat import byte_at, require_bytes

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP_CODE = 0
ICMP_HEADER_LEN = 8


def checksum(data):
    """
    Compute ICMP checksum for bytes data.
    """
    data = require_bytes(data)
    if len(data) % 2:
        data = data + b'\x00'
    total = 0
    for offset in range(0, len(data), 2):
        total += (byte_at(data, offset) << 8) + byte_at(data, offset + 1)
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_echo_packet(icmp_type, ident, seq, payload):
    """
    Build an ICMP Echo packet with the given type, id, seq, and payload.
    """
    payload = require_bytes(payload)
    header = struct.pack('>BBHHH', icmp_type, ICMP_CODE, 0, ident, seq)
    csum = checksum(header + payload)
    header = struct.pack('>BBHHH', icmp_type, ICMP_CODE, csum, ident, seq)
    return header + payload


def build_echo_request(ident, seq, payload):
    """Build an ICMP Echo Request packet."""
    return build_echo_packet(ICMP_ECHO_REQUEST, ident, seq, payload)


def build_echo_reply(ident, seq, payload):
    """Build an ICMP Echo Reply packet."""
    return build_echo_packet(ICMP_ECHO_REPLY, ident, seq, payload)


def _extract_icmp(data):
    """
    Extract ICMP bytes from a raw IP packet if present.
    """
    data = require_bytes(data)
    if len(data) < ICMP_HEADER_LEN:
        return None

    first = byte_at(data, 0)
    version = first >> 4
    if version == 4:
        if len(data) < 20:
            return None
        ihl = first & 0x0F
        ip_header_len = ihl * 4
        if ip_header_len < 20:
            return None
        if len(data) < ip_header_len + ICMP_HEADER_LEN:
            return None
        proto = byte_at(data, 9)
        if proto != socket.IPPROTO_ICMP:
            return None
        return data[ip_header_len:]
    if version == 6:
        return None

    return data


def parse_icmp_echo(data, expect_type=None):
    """
    Parse an ICMP Echo Request/Reply packet.

    Returns:
        tuple: (icmp_type, ident, seq, payload) or None on parse failure.
    """
    icmp = _extract_icmp(data)
    if icmp is None or len(icmp) < ICMP_HEADER_LEN:
        return None
    if checksum(icmp) != 0:
        return None

    icmp_type, code, _, ident, seq = struct.unpack(
        '>BBHHH', icmp[:ICMP_HEADER_LEN]
    )
    if code != ICMP_CODE:
        return None
    if expect_type is not None and icmp_type != expect_type:
        return None
    payload = icmp[ICMP_HEADER_LEN:]
    return icmp_type, ident, seq, payload
