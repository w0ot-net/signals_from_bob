# -*- coding: ascii -*-
"""
ICMP packet helpers for Echo Request/Reply.
"""

from __future__ import absolute_import

import array
import socket
import struct
import sys

from ...compat import (
    array_frombytes,
    buffer_view,
    byte_at,
    require_bytes_like,
    require_bytes_like_or_bytearray,
    to_bytes,
)

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP_CODE = 0
ICMP_HEADER_LEN = 8


def _sum_words(data):
    words = array.array('H')
    array_frombytes(words, data)
    if sys.byteorder == 'little':
        # Array uses native endianness; checksum needs network byte order.
        words.byteswap()
    return sum(words)


def _checksum_buffer(data):
    total = 0
    length = len(data)
    if length % 2:
        total += byte_at(data, length - 1) << 8
        length -= 1
    if length:
        view = buffer_view(data, length)
        if sys.byteorder == 'big' and getattr(view, 'itemsize', None) == 1:
            cast = getattr(view, 'cast', None)
            if cast is not None:
                try:
                    total += sum(cast('H'))
                except (TypeError, ValueError):
                    total += _sum_words(view)
            else:
                total += _sum_words(view)
        else:
            total += _sum_words(view)
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def checksum(data):
    """
    Compute ICMP checksum for bytes data.
    """
    data = require_bytes_like(data)
    return _checksum_buffer(data)


def build_echo_packet(icmp_type, ident, seq, payload):
    """
    Build an ICMP Echo packet with the given type, id, seq, and payload.
    """
    payload = require_bytes_like_or_bytearray(payload)
    packet_len = ICMP_HEADER_LEN + len(payload)
    packet = bytearray(packet_len)
    struct.pack_into('>BBHHH', packet, 0, icmp_type, ICMP_CODE, 0, ident, seq)
    if payload:
        packet[ICMP_HEADER_LEN:] = payload
    csum = _checksum_buffer(packet)
    struct.pack_into('>BBHHH', packet, 0, icmp_type, ICMP_CODE, csum, ident, seq)
    return require_bytes_like(packet)


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
    data = require_bytes_like(data)
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


def parse_icmp_echo(data, expect_type=None, expect_ident=None,
                    validate_checksum=False):
    """
    Parse an ICMP Echo Request/Reply packet.

    Args:
        expect_type: Optional ICMP type to match before checksum.
        expect_ident: Optional ICMP id to match before checksum.
        validate_checksum: True to reject packets with bad ICMP checksums.

    Returns:
        tuple: (icmp_type, ident, seq, payload) or None on parse failure.
    """
    icmp = _extract_icmp(data)
    if icmp is None or len(icmp) < ICMP_HEADER_LEN:
        return None
    icmp_type, code, _, ident, seq = struct.unpack(
        '>BBHHH', icmp[:ICMP_HEADER_LEN]
    )
    if code != ICMP_CODE:
        return None
    if expect_type is not None and icmp_type != expect_type:
        return None
    if expect_ident is not None and ident != expect_ident:
        return None
    if validate_checksum and checksum(icmp) != 0:
        return None
    payload = to_bytes(icmp[ICMP_HEADER_LEN:])
    return icmp_type, ident, seq, payload
