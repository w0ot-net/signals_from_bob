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
QTYPE_A = 1
QTYPE_CNAME = 5
QTYPE_SOA = 6
QTYPE_NULL = 10
QTYPE_TXT = 16
QTYPE_AAAA = 28
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
DEFAULT_LABEL_MAX_LEN = 50
MAX_NAME_LEN = 253
NONCE_LEN = 4

RECORD_TYPES = {
    'A': QTYPE_A,
    'AAAA': QTYPE_AAAA,
    'CNAME': QTYPE_CNAME,
    'NULL': QTYPE_NULL,
    'TXT': QTYPE_TXT,
}


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


def _normalize_label_max_len(label_max_len):
    if label_max_len is None:
        return DEFAULT_LABEL_MAX_LEN
    if label_max_len < NONCE_LEN:
        raise ValueError('label_max_len must be >= %d' % NONCE_LEN)
    if label_max_len > MAX_LABEL_LEN:
        raise ValueError('label_max_len must be <= %d' % MAX_LABEL_LEN)
    return label_max_len


def encode_query_name(data, base_domain, nonce, label_max_len=None):
    """
    Encode tunnel data into DNS query name.

    Args:
        data: bytes to encode
        base_domain: tunnel domain suffix
        nonce: 16-bit nonce value for cache busting
        label_max_len: max label length for tunnel data labels

    Returns:
        str: complete query name
    """
    label_max_len = _normalize_label_max_len(label_max_len)

    # Generate nonce label
    nonce_label = base32_encode(struct.pack('>H', nonce & 0xFFFF))[:NONCE_LEN]

    # Base32 encode data
    b32 = base32_encode(data)

    # Split into labels respecting max length
    labels = [nonce_label]
    while b32:
        labels.append(b32[:label_max_len])
        b32 = b32[label_max_len:]

    # Append base domain
    labels.extend(base_domain.split('.'))
    return '.'.join(labels)


def decode_query_name(query_name, base_domain, label_max_len=None):
    """
    Decode tunnel data from DNS query name.

    Args:
        query_name: full query name
        base_domain: tunnel domain suffix to strip
        label_max_len: max label length for tunnel data labels

    Returns:
        bytes: decoded tunnel data
    """
    # Remove base domain suffix
    base_parts = base_domain.lower().split('.')
    name_parts = query_name.lower().split('.')

    # Verify suffix matches
    if name_parts[-len(base_parts):] != base_parts:
        raise ValueError('Query name does not match base domain')

    label_max_len = _normalize_label_max_len(label_max_len)

    # Get data labels (skip nonce at index 0, skip base domain at end)
    data_labels = name_parts[1:-len(base_parts)]
    for label in data_labels:
        if len(label) > label_max_len:
            raise ValueError('Label exceeds max length')

    # Concatenate and decode
    b32 = ''.join(data_labels)
    if not b32:
        raise ValueError('No data labels in query name')
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


def encode_a_rdata(addr_bytes):
    """Encode IPv4 address bytes as A RDATA."""
    if len(addr_bytes) != 4:
        raise ValueError('A RDATA requires 4 bytes')
    return addr_bytes


def calc_query_mtu(base_domain, label_max_len=None):
    """
    Calculate max tunnel bytes for a query.

    Args:
        base_domain: tunnel domain suffix
        label_max_len: max label length for tunnel data labels

    Returns:
        int: max bytes that can be encoded in a query
    """
    label_max_len = _normalize_label_max_len(label_max_len)

    # Available chars after base domain, trailing dot, nonce, and nonce dot
    available = MAX_NAME_LEN - len(base_domain) - 1 - NONCE_LEN - 1
    if available <= 0:
        return 0
    # Dots between labels
    label_overhead = available // (label_max_len + 1)
    usable = available - label_overhead
    # Base32: 5 bytes -> 8 chars
    return (usable * 5) // 8


def encode_cname_target(data, cname_suffix, label_max_len=None):
    """
    Encode tunnel data into a CNAME target name.

    Args:
        data: bytes to encode
        cname_suffix: suffix appended to data labels
        label_max_len: max label length for tunnel data labels

    Returns:
        str: CNAME target name
    """
    label_max_len = _normalize_label_max_len(label_max_len)

    b32 = base32_encode(data)
    labels = []
    while b32:
        labels.append(b32[:label_max_len])
        b32 = b32[label_max_len:]

    suffix = cname_suffix.strip('.')
    if suffix:
        labels.extend(suffix.split('.'))
    return '.'.join(labels)


def decode_cname_target(target_name, cname_suffix, label_max_len=None):
    """
    Decode tunnel data from a CNAME target name.

    Args:
        target_name: full CNAME target name
        cname_suffix: suffix appended to data labels
        label_max_len: max label length for tunnel data labels

    Returns:
        bytes: decoded tunnel data
    """
    label_max_len = _normalize_label_max_len(label_max_len)

    suffix_parts = cname_suffix.lower().strip('.').split('.')
    name_parts = target_name.lower().strip('.').split('.')

    if suffix_parts != [''] and suffix_parts:
        if name_parts[-len(suffix_parts):] != suffix_parts:
            raise ValueError('CNAME target does not match suffix')
        data_parts = name_parts[:-len(suffix_parts)]
    else:
        data_parts = name_parts

    for label in data_parts:
        if len(label) > label_max_len:
            raise ValueError('Label exceeds max length')

    b32 = ''.join(data_parts)
    if not b32:
        raise ValueError('No data labels in CNAME target')
    return base32_decode(b32)


def calc_response_mtu(rtype, edns_size=512, cname_suffix=None,
                      label_max_len=None):
    """
    Calculate max tunnel bytes for a response.

    Args:
        rtype: response record type
        edns_size: UDP buffer size (512 standard, 4096 with EDNS0)

    Returns:
        int: max bytes that can be encoded in a response
    """
    if rtype in (QTYPE_A, QTYPE_AAAA):
        return 0
    if rtype == QTYPE_NULL:
        # NULL: raw base64 without padding
        overhead = 45  # DNS header + answer overhead
        available = edns_size - overhead
        if available <= 0:
            return 0
        return (available * 3) // 4
    if rtype == QTYPE_TXT:
        if edns_size <= 512:
            # Standard: single TXT string
            return (255 * 3) // 4
        # EDNS0: larger response
        overhead = 45  # DNS header + answer overhead
        available = edns_size - overhead
        return (available * 3) // 4
    if rtype == QTYPE_CNAME:
        if cname_suffix is None:
            raise ValueError('cname_suffix required for CNAME MTU')
        label_max_len = _normalize_label_max_len(label_max_len)
        suffix = cname_suffix.strip('.')
        available = MAX_NAME_LEN - len(suffix) - 1
        if available <= 0:
            return 0
        label_overhead = available // (label_max_len + 1)
        usable = available - label_overhead
        return (usable * 5) // 8
    raise ValueError('Unsupported record type')


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
