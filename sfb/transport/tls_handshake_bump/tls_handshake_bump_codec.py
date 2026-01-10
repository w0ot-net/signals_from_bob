# -*- coding: ascii -*-
"""
TLS handshake bump transport codec.

Encodes packet bytes into base32 (lowercase, no padding) for SNI and CN,
and builds/parses minimal TLS handshake messages for SNI extraction and
certificate delivery.
"""

from __future__ import absolute_import

import os
import re
import struct
import zlib

from ..base32 import base32_decode as shared_base32_decode
from ..base32 import base32_decode_bytes as shared_base32_decode_bytes
from ..base32 import base32_encode as shared_base32_encode
from ...compat import (
    byte_at,
    bytearray_to_bytes,
    require_bytes_like_or_bytearray,
    text_type,
    to_bytes,
)


TLS_CONTENT_TYPE_HANDSHAKE = 0x16
TLS_VERSION_1_0 = 0x0301
TLS_VERSION_1_1 = 0x0302
TLS_VERSION_1_2 = 0x0303

TLS_HANDSHAKE_CLIENT_HELLO = 0x01
TLS_HANDSHAKE_SERVER_HELLO = 0x02
TLS_HANDSHAKE_CERTIFICATE = 0x0b
TLS_HANDSHAKE_SERVER_HELLO_DONE = 0x0e

TLS_RECORD_HEADER_LEN = 5
TLS_HANDSHAKE_HEADER_LEN = 4
TLS_MAX_RECORD_PAYLOAD = 16384
TLS_MAX_RECORD_SIZE = TLS_RECORD_HEADER_LEN + TLS_MAX_RECORD_PAYLOAD

TLS_RSA_WITH_AES_128_CBC_SHA = 0x002F

EXT_SERVER_NAME = 0x0000

MAX_LABEL_LEN = 63
MAX_NAME_LEN = 253

SFB_BUMP_VERSION = 0x01
SFB_BUMP_HEADER_LEN = 3
SFB_BUMP_RESPONSE_VERSION = 0x01
SFB_BUMP_RESPONSE_HEADER_LEN = 5
SFB_BUMP_RESPONSE_TOKEN_MIN_LEN = (SFB_BUMP_RESPONSE_HEADER_LEN * 8 + 4) // 5

_DOMAIN_ALLOWED = re.compile(r'^[A-Za-z0-9.-]+$')
_RESPONSE_TOKEN_RE = re.compile(
    b'[A-Za-z2-7]{%d,}' % SFB_BUMP_RESPONSE_TOKEN_MIN_LEN
)

_BASE32_ALPHABET = b'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
_BASE32_DECODE_TABLE = [-1] * 256
for _index in range(len(_BASE32_ALPHABET)):
    _code = byte_at(_BASE32_ALPHABET, _index)
    _BASE32_DECODE_TABLE[_code] = _index
    if 65 <= _code <= 90:
        _BASE32_DECODE_TABLE[_code + 32] = _index


def base32_encode(data):
    """Encode bytes to base32 without padding, lowercase."""
    return shared_base32_encode(data, lowercase=True)


def base32_decode(value):
    """Decode base32 string to bytes (handles missing padding)."""
    return shared_base32_decode(value)


_base32_decode_bytes = shared_base32_decode_bytes


def encode_sni_name(payload, base_domain):
    """
    Encode payload into an SNI name under base_domain.
    """
    base_domain = normalize_domain(base_domain)
    return encode_sni_name_with_labels(payload, base_domain.split('.'))


def encode_sni_name_with_labels(payload, base_labels):
    """
    Encode payload into an SNI name using a normalized base domain label list.
    """
    payload = to_bytes(payload)
    encoded = _encode_payload(payload)
    labels = _split_labels(encoded, MAX_LABEL_LEN)
    labels.extend(base_labels)
    _validate_name_length(labels)
    return '.'.join(labels)


def decode_sni_name(name, base_domain):
    """
    Decode payload from an SNI name under base_domain.
    """
    base_domain = normalize_domain(base_domain)
    return decode_sni_name_with_base(name, base_domain)


def decode_sni_name_with_base(name, base_domain):
    """
    Decode payload from an SNI name using a normalized base domain.
    """
    if not isinstance(name, text_type):
        raise TypeError('SNI must be text')
    try:
        name.encode('ascii')
    except UnicodeError:
        raise ValueError('SNI must be ASCII')
    name = _strip_trailing_dot(name).lower()
    if name == base_domain:
        raise ValueError('SNI payload missing')
    suffix = '.' + base_domain
    if not name.endswith(suffix):
        raise ValueError('SNI does not match base domain')
    prefix = name[:-len(suffix)]
    if not prefix or prefix.endswith('.'):
        raise ValueError('SNI payload missing')
    labels = prefix.split('.')
    _validate_labels(labels)
    encoded = ''.join(labels)
    if not encoded:
        raise ValueError('SNI payload missing')
    return _decode_payload(encoded)


def encode_cn_value(payload, max_len=None):
    """
    Encode payload into a base32 CN string with checksum framing.
    """
    payload = to_bytes(payload)
    encoded = _encode_response_payload(payload)
    if max_len is not None and len(encoded) > max_len:
        raise ValueError('CN exceeds max length')
    return encoded


def decode_cn_value(value):
    """
    Decode payload from a base32 CN string with checksum framing.
    """
    if not isinstance(value, text_type):
        raise TypeError('CN must be text')
    return _decode_response_payload(value)


def scan_response_payload(data, max_payload_len=None, max_token_len=None,
                          start_offset=0):
    """
    Scan bytes for a valid response token and return payload bytes.

    start_offset: optional byte offset to begin scanning.
    """
    if isinstance(data, text_type):
        raise TypeError('Expected bytes for response scan')
    if isinstance(data, (bytes, bytearray)):
        scan_bytes = data
    else:
        scan_bytes = to_bytes(data)
    if isinstance(scan_bytes, bytearray):
        scan_bytes = bytearray_to_bytes(scan_bytes)
    if start_offset is None or start_offset < 0:
        start_offset = 0
    min_len = SFB_BUMP_RESPONSE_TOKEN_MIN_LEN
    if max_token_len is not None and max_token_len < min_len:
        return None
    if start_offset >= len(scan_bytes):
        return None
    for match in _RESPONSE_TOKEN_RE.finditer(scan_bytes, start_offset):
        token_bytes = match.group(0)
        token_len = len(token_bytes)
        if token_len < min_len:
            continue
        max_start = token_len - min_len
        start = 0
        while start <= max_start:
            payload_len = _try_decode_response_header(token_bytes, start)
            if payload_len is None:
                start += 1
                continue
            if max_payload_len is not None and payload_len > max_payload_len:
                start += 1
                continue
            encoded_len = _base32_len(payload_len + SFB_BUMP_RESPONSE_HEADER_LEN)
            if encoded_len < min_len:
                start += 1
                continue
            if max_token_len is not None and encoded_len > max_token_len:
                start += 1
                continue
            end = start + encoded_len
            if end > token_len:
                start += 1
                continue
            payload = _try_decode_response_token(
                token_bytes[start:end],
                max_payload_len=max_payload_len,
            )
            if payload is not None:
                return payload
            start += 1
    return None


def calc_sni_payload_cap(base_domain, label_max_len=MAX_LABEL_LEN,
                         max_name_len=MAX_NAME_LEN):
    """
    Calculate max payload bytes for SNI encoding under base_domain.
    """
    base_domain = normalize_domain(base_domain)
    base_len = len(base_domain)
    if base_len >= max_name_len:
        return 0
    max_prefix_len = max_name_len - base_len - 1
    if max_prefix_len <= 0:
        return 0
    max_bytes = (max_prefix_len * 5) // 8
    if max_bytes <= SFB_BUMP_HEADER_LEN:
        return 0
    low = 0
    high = max_bytes - SFB_BUMP_HEADER_LEN
    best = 0
    while low <= high:
        mid = (low + high) // 2
        encoded_len = _base32_len(mid + SFB_BUMP_HEADER_LEN)
        dots = 0
        if encoded_len:
            dots = (encoded_len - 1) // label_max_len
        total = encoded_len + dots
        if total <= max_prefix_len:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def calc_cn_payload_cap(cn_max_len):
    """
    Calculate max payload bytes for CN encoding.
    """
    if cn_max_len is None:
        return 0
    try:
        cn_max_len = int(cn_max_len)
    except (TypeError, ValueError):
        return 0
    if cn_max_len <= 0:
        return 0
    max_bytes = (cn_max_len * 5) // 8
    if max_bytes <= SFB_BUMP_RESPONSE_HEADER_LEN:
        return 0
    low = 0
    high = max_bytes - SFB_BUMP_RESPONSE_HEADER_LEN
    best = 0
    while low <= high:
        mid = (low + high) // 2
        encoded_len = _base32_len(mid + SFB_BUMP_RESPONSE_HEADER_LEN)
        if encoded_len <= cn_max_len:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def parse_record_header(header, max_record_bytes=None):
    """
    Parse a TLS record header.
    """
    header = to_bytes(header)
    if len(header) != TLS_RECORD_HEADER_LEN:
        raise ValueError('Invalid TLS record header length')
    content_type, version, length = struct.unpack('!BHH', header)
    if content_type != TLS_CONTENT_TYPE_HANDSHAKE:
        raise ValueError('Invalid TLS content type')
    if version < TLS_VERSION_1_0 or version > TLS_VERSION_1_2:
        raise ValueError('Invalid TLS version')
    if length > TLS_MAX_RECORD_PAYLOAD:
        raise ValueError('TLS record too large')
    if max_record_bytes is not None:
        if length + TLS_RECORD_HEADER_LEN > max_record_bytes:
            raise ValueError('TLS record exceeds configured max')
    return length


def parse_client_hello_sni(record, max_record_bytes=None):
    """
    Parse a TLS ClientHello record and return the SNI string.
    """
    record = to_bytes(record)
    if len(record) < TLS_RECORD_HEADER_LEN:
        raise ValueError('Record too short')
    length = parse_record_header(record[:TLS_RECORD_HEADER_LEN],
                                 max_record_bytes=max_record_bytes)
    return parse_client_hello_sni_from_buffer(
        record,
        length,
        max_record_bytes=max_record_bytes,
    )


def parse_client_hello_sni_from_buffer(record, record_len, max_record_bytes=None):
    """
    Parse a TLS ClientHello record from an existing buffer.

    record_len is the payload length from the already parsed record header.
    """
    record = require_bytes_like_or_bytearray(record)
    if record_len is None or record_len < 0:
        raise ValueError('TLS record length invalid')
    if record_len > TLS_MAX_RECORD_PAYLOAD:
        raise ValueError('TLS record too large')
    total_len = TLS_RECORD_HEADER_LEN + record_len
    if max_record_bytes is not None and total_len > max_record_bytes:
        raise ValueError('TLS record exceeds configured max')
    if len(record) < total_len:
        raise ValueError('Record length mismatch')
    offset = TLS_RECORD_HEADER_LEN
    if offset + TLS_HANDSHAKE_HEADER_LEN > total_len:
        raise ValueError('Handshake header truncated')
    handshake_type = byte_at(record, offset)
    if handshake_type != TLS_HANDSHAKE_CLIENT_HELLO:
        raise ValueError('Handshake type invalid')
    handshake_len = _unpack_u24(record[offset + 1:offset + 4])
    if record_len != TLS_HANDSHAKE_HEADER_LEN + handshake_len:
        raise ValueError('Handshake length mismatch')
    body_start = offset + TLS_HANDSHAKE_HEADER_LEN
    body_end = body_start + handshake_len
    if body_end != total_len:
        raise ValueError('Handshake body truncated')
    return _parse_client_hello_body_for_sni_from_buffer(record, body_start, body_end)


def build_server_handshake_record(cert_der, random_bytes=None,
                                  cipher_suite=TLS_RSA_WITH_AES_128_CBC_SHA):
    """
    Build a TLS ServerHello + Certificate + ServerHelloDone record.
    """
    cert_der = to_bytes(cert_der)
    if random_bytes is None:
        random_bytes = os.urandom(32)
    random_bytes = to_bytes(random_bytes)
    if len(random_bytes) != 32:
        raise ValueError('ServerHello random must be 32 bytes')
    server_hello = _build_server_hello(random_bytes, cipher_suite)
    certificate = _build_certificate(cert_der)
    done = _build_server_hello_done()
    handshake = server_hello + certificate + done
    if len(handshake) > TLS_MAX_RECORD_PAYLOAD:
        raise ValueError('Handshake record too large')
    return (
        struct.pack('!BHH', TLS_CONTENT_TYPE_HANDSHAKE,
                    TLS_VERSION_1_2, len(handshake)) +
        handshake
    )


def normalize_domain(name):
    """
    Normalize and validate a base domain.
    """
    if not isinstance(name, text_type):
        raise ValueError('Base domain must be text')
    try:
        name.encode('ascii')
    except UnicodeError:
        raise ValueError('Base domain must be ASCII')
    name = _strip_trailing_dot(name)
    if not name:
        raise ValueError('Base domain required')
    if name.startswith('.') or name.endswith('.'):
        raise ValueError('Base domain must not start/end with dot')
    if '..' in name:
        raise ValueError('Base domain must not contain empty labels')
    if not _DOMAIN_ALLOWED.match(name):
        raise ValueError('Base domain contains invalid characters')
    labels = name.split('.')
    _validate_labels(labels)
    _validate_name_length(labels)
    return name.lower()


def _strip_trailing_dot(name):
    if name.endswith('.'):
        return name[:-1]
    return name


def _validate_labels(labels):
    for label in labels:
        if not label:
            raise ValueError('Empty label in name')
        if len(label) > MAX_LABEL_LEN:
            raise ValueError('Label exceeds max length')


def _validate_name_length(labels):
    total_len = 0
    for label in labels:
        total_len += len(label)
    total_len += (len(labels) - 1)
    if total_len > MAX_NAME_LEN:
        raise ValueError('Name exceeds max length')


def _split_labels(text, label_max_len):
    if label_max_len <= 0:
        raise ValueError('label_max_len must be positive')
    labels = []
    while text:
        labels.append(text[:label_max_len])
        text = text[label_max_len:]
    return labels


def _encode_payload(payload):
    if len(payload) > 0xFFFF:
        raise ValueError('Payload too large')
    data = (
        struct.pack('!B', SFB_BUMP_VERSION) +
        struct.pack('!H', len(payload)) +
        payload
    )
    return base32_encode(data)


def _decode_payload(text):
    decoded = base32_decode(text)
    if len(decoded) < SFB_BUMP_HEADER_LEN:
        raise ValueError('Payload header truncated')
    version = byte_at(decoded, 0)
    if version != SFB_BUMP_VERSION:
        raise ValueError('Payload version invalid')
    payload_len = struct.unpack('!H', decoded[1:3])[0]
    if payload_len != len(decoded) - SFB_BUMP_HEADER_LEN:
        raise ValueError('Payload length mismatch')
    return decoded[SFB_BUMP_HEADER_LEN:]


def _encode_response_payload(payload):
    if len(payload) > 0xFFFF:
        raise ValueError('Payload too large')
    checksum = _response_checksum(payload)
    data = (
        struct.pack('!B', SFB_BUMP_RESPONSE_VERSION) +
        struct.pack('!H', len(payload)) +
        struct.pack('!H', checksum) +
        payload
    )
    return base32_encode(data)


def _decode_response_payload(text, max_payload_len=None):
    decoded = base32_decode(text)
    if len(decoded) < SFB_BUMP_RESPONSE_HEADER_LEN:
        raise ValueError('Response header truncated')
    version = byte_at(decoded, 0)
    if version != SFB_BUMP_RESPONSE_VERSION:
        raise ValueError('Response version invalid')
    payload_len = struct.unpack('!H', decoded[1:3])[0]
    checksum = struct.unpack('!H', decoded[3:5])[0]
    payload = decoded[SFB_BUMP_RESPONSE_HEADER_LEN:]
    if payload_len > len(payload):
        raise ValueError('Response length mismatch')
    if payload_len < len(payload):
        extra = payload[payload_len:]
        if extra.rstrip(b'\x00'):
            raise ValueError('Response padding mismatch')
        payload = payload[:payload_len]
    if max_payload_len is not None and payload_len > max_payload_len:
        raise ValueError('Response payload too large')
    if checksum != _response_checksum(payload):
        raise ValueError('Response checksum mismatch')
    return payload


def _decode_response_payload_bytes(value, max_payload_len=None):
    decoded = _base32_decode_bytes(value)
    if len(decoded) < SFB_BUMP_RESPONSE_HEADER_LEN:
        raise ValueError('Response header truncated')
    version = byte_at(decoded, 0)
    if version != SFB_BUMP_RESPONSE_VERSION:
        raise ValueError('Response version invalid')
    payload_len = struct.unpack('!H', decoded[1:3])[0]
    checksum = struct.unpack('!H', decoded[3:5])[0]
    payload = decoded[SFB_BUMP_RESPONSE_HEADER_LEN:]
    if payload_len > len(payload):
        raise ValueError('Response length mismatch')
    if payload_len < len(payload):
        extra = payload[payload_len:]
        if extra.rstrip(b'\x00'):
            raise ValueError('Response padding mismatch')
        payload = payload[:payload_len]
    if max_payload_len is not None and payload_len > max_payload_len:
        raise ValueError('Response payload too large')
    if checksum != _response_checksum(payload):
        raise ValueError('Response checksum mismatch')
    return payload


def _try_decode_response_token(token_bytes, max_payload_len=None):
    try:
        return _decode_response_payload_bytes(
            token_bytes,
            max_payload_len=max_payload_len,
        )
    except Exception:
        return None


def _try_decode_response_header(data, start=0):
    end = start + SFB_BUMP_RESPONSE_TOKEN_MIN_LEN
    if start < 0 or end > len(data):
        return None
    v0 = _BASE32_DECODE_TABLE[byte_at(data, start)]
    if v0 < 0:
        return None
    v1 = _BASE32_DECODE_TABLE[byte_at(data, start + 1)]
    if v1 < 0:
        return None
    v2 = _BASE32_DECODE_TABLE[byte_at(data, start + 2)]
    if v2 < 0:
        return None
    v3 = _BASE32_DECODE_TABLE[byte_at(data, start + 3)]
    if v3 < 0:
        return None
    v4 = _BASE32_DECODE_TABLE[byte_at(data, start + 4)]
    if v4 < 0:
        return None
    v5 = _BASE32_DECODE_TABLE[byte_at(data, start + 5)]
    if v5 < 0:
        return None
    v6 = _BASE32_DECODE_TABLE[byte_at(data, start + 6)]
    if v6 < 0:
        return None
    v7 = _BASE32_DECODE_TABLE[byte_at(data, start + 7)]
    if v7 < 0:
        return None
    b0 = (v0 << 3) | (v1 >> 2)
    if b0 != SFB_BUMP_RESPONSE_VERSION:
        return None
    b1 = ((v1 & 0x03) << 6) | (v2 << 1) | (v3 >> 4)
    b2 = ((v3 & 0x0F) << 4) | (v4 >> 1)
    return (b1 << 8) | b2


def _response_checksum(payload):
    payload = to_bytes(payload)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return checksum & 0xFFFF


def _base32_len(byte_len):
    return (byte_len * 8 + 4) // 5


def _parse_record(record, expected_handshake_type, max_record_bytes=None):
    record = to_bytes(record)
    if len(record) < TLS_RECORD_HEADER_LEN:
        raise ValueError('Record too short')
    length = parse_record_header(record[:TLS_RECORD_HEADER_LEN],
                                 max_record_bytes=max_record_bytes)
    if len(record) != TLS_RECORD_HEADER_LEN + length:
        raise ValueError('Record length mismatch')
    offset = TLS_RECORD_HEADER_LEN
    if offset + TLS_HANDSHAKE_HEADER_LEN > len(record):
        raise ValueError('Handshake header truncated')
    handshake_type = byte_at(record, offset)
    if handshake_type != expected_handshake_type:
        raise ValueError('Handshake type invalid')
    handshake_len = _unpack_u24(record[offset + 1:offset + 4])
    if length != TLS_HANDSHAKE_HEADER_LEN + handshake_len:
        raise ValueError('Handshake length mismatch')
    body_start = offset + TLS_HANDSHAKE_HEADER_LEN
    body_end = body_start + handshake_len
    if body_end != len(record):
        raise ValueError('Handshake body truncated')
    return handshake_type, record[body_start:body_end]


def _parse_client_hello_body_for_sni(body):
    body = to_bytes(body)
    return _parse_client_hello_body_for_sni_from_buffer(body, 0, len(body))


def _parse_client_hello_body_for_sni_from_buffer(data, offset, end):
    legacy_version, offset = _read_u16(data, offset)
    if legacy_version < TLS_VERSION_1_0 or legacy_version > TLS_VERSION_1_2:
        raise ValueError('Invalid ClientHello version')
    if offset + 32 > end:
        raise ValueError('ClientHello random truncated')
    offset += 32
    session_id_len, offset = _read_u8(data, offset)
    if offset + session_id_len > end:
        raise ValueError('ClientHello session id truncated')
    offset += session_id_len
    cipher_suites_len, offset = _read_u16(data, offset)
    if offset + cipher_suites_len > end:
        raise ValueError('ClientHello cipher suite list truncated')
    offset += cipher_suites_len
    compression_methods_len, offset = _read_u8(data, offset)
    if offset + compression_methods_len > end:
        raise ValueError('ClientHello compression list truncated')
    offset += compression_methods_len
    extensions_len, offset = _read_u16(data, offset)
    if offset + extensions_len != end:
        raise ValueError('ClientHello extensions length invalid')
    return _parse_extensions_for_sni(data, offset, end)


def _parse_extensions_for_sni(data, offset, end):
    while offset < end:
        if offset + 4 > end:
            raise ValueError('Extension header truncated')
        ext_type = struct.unpack('!H', data[offset:offset + 2])[0]
        ext_len = struct.unpack('!H', data[offset + 2:offset + 4])[0]
        offset += 4
        if offset + ext_len > end:
            raise ValueError('Extension data truncated')
        ext_data = data[offset:offset + ext_len]
        if ext_type == EXT_SERVER_NAME:
            return _parse_sni_extension(ext_data)
        offset += ext_len
    raise ValueError('Missing SNI extension')


def _parse_sni_extension(data):
    if len(data) < 2:
        raise ValueError('SNI extension truncated')
    list_len = struct.unpack('!H', data[:2])[0]
    if list_len != len(data) - 2:
        raise ValueError('SNI list length invalid')
    offset = 2
    while offset < len(data):
        if offset + 3 > len(data):
            raise ValueError('SNI entry truncated')
        name_type = byte_at(data, offset)
        name_len = struct.unpack('!H', data[offset + 1:offset + 3])[0]
        offset += 3
        if offset + name_len > len(data):
            raise ValueError('SNI name truncated')
        name_bytes = data[offset:offset + name_len]
        offset += name_len
        if name_type == 0:
            try:
                return to_bytes(name_bytes).decode('ascii')
            except UnicodeError:
                raise ValueError('SNI name must be ASCII')
    raise ValueError('Missing SNI host name')


def _build_server_hello(random_bytes, cipher_suite):
    body = b''.join([
        struct.pack('!H', TLS_VERSION_1_2),
        random_bytes,
        b'\x00',  # session_id_len
        struct.pack('!H', cipher_suite),
        b'\x00',  # compression_method
        b'\x00\x00',  # extensions_len
    ])
    return struct.pack('!B', TLS_HANDSHAKE_SERVER_HELLO) + _pack_u24(len(body)) + body


def _build_certificate(cert_der):
    cert_der = to_bytes(cert_der)
    cert_entry = _pack_u24(len(cert_der)) + cert_der
    cert_list = _pack_u24(len(cert_entry)) + cert_entry
    return (struct.pack('!B', TLS_HANDSHAKE_CERTIFICATE) +
            _pack_u24(len(cert_list)) + cert_list)


def _build_server_hello_done():
    return struct.pack('!B', TLS_HANDSHAKE_SERVER_HELLO_DONE) + _pack_u24(0)


def _pack_u24(value):
    if value < 0 or value > 0xFFFFFF:
        raise ValueError('u24 out of range')
    return struct.pack('!I', value)[1:]


def _unpack_u24(data):
    if len(data) != 3:
        raise ValueError('u24 length invalid')
    return struct.unpack('!I', b'\x00' + data)[0]


def _read_u8(data, offset):
    if offset >= len(data):
        raise ValueError('Truncated u8')
    return byte_at(data, offset), offset + 1


def _read_u16(data, offset):
    if offset + 2 > len(data):
        raise ValueError('Truncated u16')
    return struct.unpack('!H', data[offset:offset + 2])[0], offset + 2
