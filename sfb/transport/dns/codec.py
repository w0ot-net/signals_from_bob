# -*- coding: ascii -*-
"""
DNS encoding and decoding utilities.

Handles base32/base64 encoding, DNS wire format, and MTU calculations.
Shared by both client and server implementations.
"""

from __future__ import absolute_import

import base64
import struct

from ...compat import byte_at

# DNS constants
QTYPE_TXT = 16
QTYPE_NULL = 10
QCLASS_IN = 1

# DNS header flags
FLAG_QR = 0x8000  # Query/Response
FLAG_AA = 0x0400  # Authoritative Answer
FLAG_RD = 0x0100  # Recursion Desired
FLAG_RA = 0x0080  # Recursion Available

# RCODE values (lower 4 bits of flags)
RCODE_MASK = 0x000F
RCODE_NOERROR = 0
RCODE_NXDOMAIN = 3
RCODE_SERVFAIL = 2

# OPT record constants (EDNS0)
QTYPE_OPT = 41
EDNS_VERSION = 0

# Limits
MAX_LABEL_LEN = 63
MAX_NAME_LEN = 253
NONCE_LEN = 4


def base32_encode(data):
    """Encode bytes to base32 without padding, uppercase."""
    return base64.b32encode(data).rstrip(b'=').decode('ascii')


def base32_decode(s):
    """Decode base32 string to bytes (handles missing padding)."""
    pad = (8 - len(s) % 8) % 8
    s = s.upper() + '=' * pad
    return base64.b32decode(s)


def base64_encode(data):
    """Encode bytes to base64 without padding."""
    return base64.b64encode(data).rstrip(b'=').decode('ascii')


def base64_decode(s):
    """Decode base64 string to bytes (handles missing padding)."""
    pad = (4 - len(s) % 4) % 4
    s = s + '=' * pad
    return base64.b64decode(s)


def encode_name(name):
    """Encode domain name to DNS wire format."""
    parts = []
    for label in name.split('.'):
        if label:
            encoded = label.encode('ascii')
            parts.append(struct.pack('B', len(encoded)) + encoded)
    parts.append(b'\x00')
    return b''.join(parts)


def decode_name(data, offset, allow_compression=True):
    """
    Decode DNS name from wire format.

    Args:
        data: bytes containing DNS message
        offset: starting offset
        allow_compression: True to allow compression pointers

    Returns:
        tuple: (name_string, new_offset)
    """
    labels = []
    jumped = False
    end_offset = None
    seen_offsets = set()

    while True:
        if offset >= len(data):
            raise ValueError('Invalid DNS name')
        length = byte_at(data, offset)

        if length == 0:
            if not jumped:
                end_offset = offset + 1
            break

        if (length & 0xC0) == 0xC0:
            # Compression pointer
            if not allow_compression:
                raise ValueError('Compression not allowed')
            if offset + 1 >= len(data):
                raise ValueError('Truncated compression pointer')
            pointer = ((length & 0x3F) << 8) | byte_at(data, offset + 1)
            if pointer >= len(data):
                raise ValueError('Compression pointer out of range')
            if pointer in seen_offsets:
                raise ValueError('Compression pointer loop')
            seen_offsets.add(pointer)
            if not jumped:
                end_offset = offset + 2
            offset = pointer
            jumped = True
            continue

        if length & 0xC0:
            raise ValueError('Invalid label length')

        offset += 1
        if offset + length > len(data):
            raise ValueError('Label exceeds data')
        labels.append(data[offset:offset + length].decode('ascii'))
        offset += length
        if not jumped:
            end_offset = offset

    name = '.'.join(labels)
    if end_offset is None:
        end_offset = offset
    return name, end_offset


def skip_name(data, offset):
    """Skip a DNS name in wire format, return new offset."""
    while offset < len(data):
        length = byte_at(data, offset)
        if length == 0:
            return offset + 1
        if (length & 0xC0) == 0xC0:
            return offset + 2
        offset += 1 + length
    raise ValueError('Invalid DNS name')


def encode_query_name(data, base_domain, nonce):
    """
    Encode tunnel data into DNS query name.

    Args:
        data: bytes to encode
        base_domain: tunnel domain suffix
        nonce: 16-bit nonce value for cache busting

    Returns:
        str: complete query name
    """
    # Generate nonce label
    nonce_label = base32_encode(struct.pack('>H', nonce & 0xFFFF))[:NONCE_LEN]

    # Base32 encode data
    b32 = base32_encode(data)

    # Split into labels respecting max length
    labels = [nonce_label]
    while b32:
        labels.append(b32[:MAX_LABEL_LEN])
        b32 = b32[MAX_LABEL_LEN:]

    # Append base domain
    labels.extend(base_domain.split('.'))
    return '.'.join(labels)


def decode_query_name(query_name, base_domain):
    """
    Decode tunnel data from DNS query name.

    Args:
        query_name: full query name
        base_domain: tunnel domain suffix to strip

    Returns:
        bytes: decoded tunnel data
    """
    # Remove base domain suffix
    base_parts = base_domain.lower().split('.')
    name_parts = query_name.lower().split('.')

    # Verify suffix matches
    if name_parts[-len(base_parts):] != base_parts:
        raise ValueError('Query name does not match base domain')

    # Get data labels (skip nonce at index 0, skip base domain at end)
    data_labels = name_parts[1:-len(base_parts)]

    # Concatenate and decode
    b32 = ''.join(data_labels)
    if not b32:
        return b''
    return base32_decode(b32)


def encode_txt_rdata(data):
    """
    Encode tunnel data as TXT record RDATA.

    Args:
        data: bytes to encode

    Returns:
        bytes: TXT RDATA (length-prefixed strings)
    """
    b64 = base64_encode(data)

    # Split into 255-char strings
    parts = []
    while b64:
        chunk = b64[:255].encode('ascii')
        parts.append(struct.pack('B', len(chunk)) + chunk)
        b64 = b64[255:]

    return b''.join(parts) if parts else b'\x00'


def decode_txt_rdata(rdata):
    """
    Decode tunnel data from TXT record RDATA.

    Args:
        rdata: bytes of TXT RDATA

    Returns:
        bytes: decoded tunnel data
    """
    strings = []
    offset = 0
    while offset < len(rdata):
        length = byte_at(rdata, offset)
        offset += 1
        if offset + length > len(rdata):
            raise ValueError('TXT string truncated')
        strings.append(rdata[offset:offset + length])
        offset += length

    b64 = b''.join(strings).decode('ascii')
    if not b64:
        return b''
    return base64_decode(b64)


def calc_query_mtu(base_domain):
    """
    Calculate max tunnel bytes for a query.

    Args:
        base_domain: tunnel domain suffix

    Returns:
        int: max bytes that can be encoded in a query
    """
    # Available chars after base domain, trailing dot, nonce, and nonce dot
    available = MAX_NAME_LEN - len(base_domain) - 1 - NONCE_LEN - 1
    # Dots between labels
    label_overhead = available // (MAX_LABEL_LEN + 1)
    usable = available - label_overhead
    # Base32: 5 bytes -> 8 chars
    return (usable * 5) // 8


def calc_response_mtu(edns_size=512):
    """
    Calculate max tunnel bytes for a response.

    Args:
        edns_size: UDP buffer size (512 standard, 4096 with EDNS0)

    Returns:
        int: max bytes that can be encoded in a response
    """
    if edns_size <= 512:
        # Standard: single TXT string
        return (255 * 3) // 4
    else:
        # EDNS0: larger response
        overhead = 45  # DNS header + answer overhead
        available = edns_size - overhead
        return (available * 3) // 4


def build_opt_record(udp_size=4096):
    """
    Build EDNS0 OPT record for additional section.

    Args:
        udp_size: Advertised UDP payload size

    Returns:
        bytes: OPT record for ARCOUNT
    """
    # OPT record format:
    # NAME: root (0x00)
    # TYPE: OPT (41)
    # CLASS: UDP payload size
    # TTL: extended RCODE (0) + version (0) + flags (0)
    # RDLENGTH: 0 (no options)
    return struct.pack('>BHHIH',
        0,              # Root name
        QTYPE_OPT,      # TYPE
        udp_size,       # CLASS = UDP size
        0,              # TTL = extended rcode/version/flags
        0               # RDLENGTH
    )
