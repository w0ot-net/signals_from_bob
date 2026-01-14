# -*- coding: ascii -*-
"""
DNS TXT encoding and decoding utilities.

Handles base32/base64 encoding, DNS wire format, TXT query/response helpers,
and MTU calculations for the TXT transport.
"""

from __future__ import absolute_import

import base64
import struct
from collections import namedtuple

from ..base32 import base32_decode as shared_base32_decode
from ..base32 import base32_encode as shared_base32_encode
from ...compat import byte_at, integer_types, require_bytes_like, text_type, to_bytes
from ...config import DNS_STANDARD_SIZE

# DNS constants
QTYPE_TXT = 16
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

# Header sizes
DNS_HEADER_LEN = 12
DNS_QUESTION_FIXED_LEN = 4
DNS_RR_FIXED_LEN = 10

# Limits
MAX_LABEL_LEN = 63
DEFAULT_LABEL_MAX_LEN = 50
MAX_NAME_LEN = 253
NONCE_LEN = 4

ParseResult = namedtuple(
    'ParseResult',
    ('dns_id', 'qname', 'payload', 'rcode', 'reason'),
)


def base32_encode(data):
    """Encode bytes to base32 without padding, uppercase."""
    return shared_base32_encode(data, lowercase=False)


def base32_decode(s):
    """Decode base32 string to bytes (handles missing padding)."""
    return shared_base32_decode(s)


def base64_encode(data):
    """Encode bytes to base64 without padding."""
    data = require_bytes_like(data)
    return base64.b64encode(data).rstrip(b'=').decode('ascii')


def base64_decode(s):
    """Decode base64 string to bytes (handles missing padding)."""
    if not isinstance(s, text_type):
        raise TypeError('Expected text for base64 decode')
    pad = (4 - len(s) % 4) % 4
    s = s + '=' * pad
    return base64.b64decode(s.encode('ascii'))


def encode_name(name):
    """Encode domain name to DNS wire format."""
    if not isinstance(name, text_type):
        raise TypeError('Expected text domain name')
    if name == '.':
        return b'\x00'
    name = _normalize_domain(name)
    parts = []
    labels = []
    for label in name.split('.'):
        if label:
            labels.append(label)
    _validate_labels(labels)
    _validate_name_length(labels)
    for label in labels:
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
    data = to_bytes(data)
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
        if length > MAX_LABEL_LEN:
            raise ValueError('Label exceeds max length')

        offset += 1
        if offset + length > len(data):
            raise ValueError('Label exceeds data')
        labels.append(data[offset:offset + length].decode('ascii'))
        offset += length
        if not jumped:
            end_offset = offset

    _validate_name_length(labels)
    name = '.'.join(labels)
    if end_offset is None:
        end_offset = offset
    return name, end_offset


def skip_name(data, offset):
    """Skip a DNS name in wire format, return new offset."""
    data = to_bytes(data)
    while offset < len(data):
        length = byte_at(data, offset)
        if length == 0:
            return offset + 1
        if (length & 0xC0) == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError('Truncated compression pointer')
            pointer = ((length & 0x3F) << 8) | byte_at(data, offset + 1)
            if pointer >= len(data):
                raise ValueError('Compression pointer out of range')
            return offset + 2
        if length & 0xC0:
            raise ValueError('Invalid label length')
        if length > MAX_LABEL_LEN:
            raise ValueError('Label exceeds max length')
        offset += 1 + length
    raise ValueError('Invalid DNS name')


def build_compression_pointer(offset):
    """Build a DNS compression pointer."""
    if not isinstance(offset, integer_types):
        raise ValueError('Compression pointer offset must be int')
    if offset < 0 or offset > 0x3FFF:
        raise ValueError('Compression pointer offset out of range')
    return struct.pack('>H', 0xC000 | offset)


def _normalize_domain(name):
    if not isinstance(name, text_type):
        try:
            name = name.decode('ascii')
        except (AttributeError, UnicodeDecodeError):
            raise TypeError('Expected text domain name')
    if name.endswith('.'):
        name = name[:-1]
    return name


def _split_domain_labels(name, lower=False, require_non_empty=False,
                         empty_error=None):
    name = _normalize_domain(name)
    if lower:
        name = name.lower()
    labels = []
    for label in name.split('.'):
        if label:
            labels.append(label)
    if require_non_empty and not labels:
        if empty_error is None:
            empty_error = 'domain required'
        raise ValueError(empty_error)
    _validate_labels(labels)
    return labels


def _validate_labels(labels, max_len=MAX_LABEL_LEN):
    for label in labels:
        if len(label) > max_len:
            raise ValueError('Label exceeds max length')


def _validate_name_length(labels):
    if not labels:
        return
    total_len = 0
    for label in labels:
        total_len += len(label)
    total_len += (len(labels) - 1)
    if total_len > MAX_NAME_LEN:
        raise ValueError('Name exceeds max length')


def _normalize_label_max_len(label_max_len):
    if label_max_len is None:
        return DEFAULT_LABEL_MAX_LEN
    if label_max_len < NONCE_LEN:
        raise ValueError('label_max_len must be >= %d' % NONCE_LEN)
    if label_max_len > MAX_LABEL_LEN:
        raise ValueError('label_max_len must be <= %d' % MAX_LABEL_LEN)
    return label_max_len


def _b32_labels(data, label_max_len):
    """Return base32-encoded labels split to label_max_len."""
    label_max_len = _normalize_label_max_len(label_max_len)
    b32 = base32_encode(data)
    labels = []
    while b32:
        labels.append(b32[:label_max_len])
        b32 = b32[label_max_len:]
    return labels


def _decode_b32_labels(name, suffix, label_max_len, skip_first=False,
                       err_suffix=None, err_no_data=None,
                       require_suffix=False, err_empty_suffix=None,
                       normalize_first=True):
    if normalize_first:
        label_max_len = _normalize_label_max_len(label_max_len)

    suffix_parts = _split_domain_labels(
        suffix,
        lower=True,
        require_non_empty=require_suffix,
        empty_error=err_empty_suffix,
    )
    name_parts = _split_domain_labels(name, lower=True)

    if suffix_parts:
        if name_parts[-len(suffix_parts):] != suffix_parts:
            raise ValueError(err_suffix)
        data_parts = name_parts[:-len(suffix_parts)]
    else:
        data_parts = name_parts

    if skip_first:
        data_parts = data_parts[1:]

    if not normalize_first:
        label_max_len = _normalize_label_max_len(label_max_len)

    for label in data_parts:
        if len(label) > label_max_len:
            raise ValueError('Label exceeds max length')

    b32 = ''.join(data_parts)
    if not b32:
        raise ValueError(err_no_data)
    return base32_decode(b32)


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
    base_labels = _split_domain_labels(
        base_domain,
        require_non_empty=True,
        empty_error='base_domain required',
    )

    # Generate nonce label
    nonce_label = base32_encode(struct.pack('>H', nonce & 0xFFFF))[:NONCE_LEN]

    labels = [nonce_label]
    labels.extend(_b32_labels(data, label_max_len))

    # Append base domain
    labels.extend(base_labels)
    _validate_name_length(labels)
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
    return _decode_b32_labels(
        query_name,
        base_domain,
        label_max_len,
        skip_first=True,
        err_suffix='Query name does not match base domain',
        err_no_data='No data labels in query name',
        require_suffix=True,
        err_empty_suffix='base_domain required',
        normalize_first=False,
    )


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
    rdata = to_bytes(rdata)
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


def build_query(query_id, qname, opt_record=None):
    """
    Build DNS TXT query packet.

    Args:
        query_id: 16-bit DNS ID
        qname: query name string
        opt_record: optional EDNS0 OPT record bytes

    Returns:
        bytes: DNS query packet
    """
    opt_record = b'' if opt_record is None else to_bytes(opt_record)
    opt_arcount = 1 if opt_record else 0
    header = struct.pack('>HHHHHH',
        query_id,
        FLAG_RD,
        1,  # QDCOUNT
        0,  # ANCOUNT
        0,  # NSCOUNT
        opt_arcount
    )
    qname_wire = encode_name(qname)
    question = qname_wire + struct.pack('>HH', QTYPE_TXT, QCLASS_IN)
    return header + question + opt_record


def parse_txt_response(data):
    """
    Parse a DNS TXT response packet.

    Returns:
        ParseResult: dns_id, qname, payload, rcode, reason for any packet
            with a readable DNS header.
        None: if packet is too short to contain a DNS header.
    """
    data = to_bytes(data)
    if len(data) < DNS_HEADER_LEN:
        return None

    query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
        '>HHHHHH', data[:DNS_HEADER_LEN]
    )

    if not (flags & FLAG_QR):
        return ParseResult(query_id, None, None, None, 'not_response')

    rcode = flags & RCODE_MASK
    if rcode != RCODE_NOERROR:
        return ParseResult(query_id, None, None, rcode, 'rcode')

    offset = DNS_HEADER_LEN
    qname = None
    try:
        for _ in range(qdcount):
            if qname is None:
                qname, offset = decode_name(
                    data, offset, allow_compression=True
                )
                qname = qname.lower()
            else:
                offset = skip_name(data, offset)
            offset += DNS_QUESTION_FIXED_LEN
    except ValueError:
        return ParseResult(query_id, None, None, None, 'question_parse')

    if qname is None:
        return ParseResult(query_id, None, None, None, 'question_parse')

    if ancount < 1:
        return ParseResult(query_id, qname, None, rcode, 'no_answer')

    for _ in range(ancount):
        try:
            answer_name, offset = decode_name(
                data, offset, allow_compression=True
            )
        except ValueError:
            return ParseResult(query_id, qname, None, rcode, 'answer_name')

        if offset + DNS_RR_FIXED_LEN > len(data):
            return ParseResult(query_id, qname, None, rcode, 'answer_header')

        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', data[offset:offset + DNS_RR_FIXED_LEN]
        )
        offset += DNS_RR_FIXED_LEN

        if offset + rdlength > len(data):
            return ParseResult(
                query_id,
                qname,
                None,
                rcode,
                'answer_rdlength',
            )

        if rclass != QCLASS_IN or rtype != QTYPE_TXT:
            offset += rdlength
            continue

        if answer_name.lower() != qname:
            offset += rdlength
            continue

        try:
            payload = decode_txt_rdata(data[offset:offset + rdlength])
        except ValueError:
            return ParseResult(
                query_id,
                qname,
                None,
                rcode,
                'payload_decode',
            )

        return ParseResult(query_id, qname, payload, rcode, 'ok')

    return ParseResult(
        query_id,
        qname,
        None,
        rcode,
        'no_matching_answer',
    )


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
    base_domain = _normalize_domain(base_domain)
    base_labels = []
    for label in base_domain.split('.'):
        if label:
            base_labels.append(label)
    _validate_labels(base_labels)

    # Available chars after base domain, trailing dot, nonce, and nonce dot
    available = MAX_NAME_LEN - len(base_domain) - 1 - NONCE_LEN - 1
    if available <= 0:
        return 0
    # Dots between labels
    label_overhead = available // (label_max_len + 1)
    usable = available - label_overhead
    # Base32: 5 bytes -> 8 chars
    return (usable * 5) // 8


def _qname_wire_len_for_payload(payload_len, base_domain, label_max_len):
    if payload_len <= 0:
        payload = b''
    else:
        payload = b'\x00' * payload_len
    qname = encode_query_name(payload, base_domain, 0, label_max_len)
    return len(encode_name(qname))


def calc_qname_wire_len(payload_len, base_domain, label_max_len=None):
    """
    Calculate QNAME wire length for an encoded query payload.

    Args:
        payload_len: query payload length in bytes
        base_domain: tunnel domain suffix
        label_max_len: max label length for tunnel data labels

    Returns:
        int: QNAME wire length in bytes
    """
    label_max_len = _normalize_label_max_len(label_max_len)
    base_domain = _normalize_domain(base_domain)
    return _qname_wire_len_for_payload(payload_len, base_domain, label_max_len)


def calc_response_mtu(rtype, edns_size=DNS_STANDARD_SIZE):
    """
    Calculate max tunnel bytes for a TXT response.

    Args:
        rtype: response record type (must be TXT)
        edns_size: UDP buffer size (512 standard, 4096 with EDNS0)

    Returns:
        int: max bytes that can be encoded in a response
    """
    if rtype != QTYPE_TXT:
        raise ValueError('Unsupported record type')
    if edns_size <= DNS_STANDARD_SIZE:
        # Standard: single TXT string
        return (255 * 3) // 4
    # EDNS0: larger response
    overhead = 45  # DNS header + answer overhead
    available = edns_size - overhead
    if available <= 0:
        return 0
    # Each 255-byte chunk adds a length byte.
    length_bytes = (available + 255) // 256
    max_b64 = available - length_bytes
    return (max_b64 * 3) // 4


def _calc_base64_len(payload_len):
    return (payload_len * 4 + 2) // 3


def _calc_txt_rdata_len(payload_len):
    if payload_len <= 0:
        return 1
    b64_len = _calc_base64_len(payload_len)
    chunks = b64_len // 255
    if b64_len % 255:
        chunks += 1
    return b64_len + chunks


def calc_txt_response_payload_cap(qname_wire_len, edns_size,
                                  opt_record_len=0):
    """
    Calculate response payload cap for a TXT response.

    Args:
        qname_wire_len: wire length of QNAME
        edns_size: advertised EDNS UDP size
        opt_record_len: encoded OPT record length when EDNS is enabled

    Returns:
        tuple: (response_payload_cap, max_packet_size)
    """
    if qname_wire_len is None:
        return None, None
    max_packet_size = edns_size
    if max_packet_size < DNS_STANDARD_SIZE:
        max_packet_size = DNS_STANDARD_SIZE
    additional_len = 0
    if edns_size > DNS_STANDARD_SIZE and opt_record_len:
        additional_len = opt_record_len
    question_len = qname_wire_len + 4
    answer_name_len = 2  # Compression pointer
    answer_fixed_len = 10
    fixed_len = (12 + question_len + answer_name_len +
                 answer_fixed_len + additional_len)
    if fixed_len >= max_packet_size:
        return 0, max_packet_size
    available = max_packet_size - fixed_len
    upper = available
    max_payload = 0
    for payload_len in range(1, upper + 1):
        if _calc_txt_rdata_len(payload_len) > available:
            break
        max_payload = payload_len
    return max_payload, max_packet_size
