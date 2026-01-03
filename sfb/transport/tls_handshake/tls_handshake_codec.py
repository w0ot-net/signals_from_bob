# -*- coding: ascii -*-
"""
TLS ClientHello transport codec.

Encodes and decodes minimal TLS 1.2 ClientHello/ServerHello records that
carry SFB packet bytes in a private-use extension.
"""

from __future__ import absolute_import

import os
import struct

from ...compat import byte_at, text_type, to_bytes


TLS_CONTENT_TYPE_HANDSHAKE = 0x16
TLS_VERSION_1_2 = 0x0303
TLS_HANDSHAKE_CLIENT_HELLO = 0x01
TLS_HANDSHAKE_SERVER_HELLO = 0x02

TLS_RECORD_HEADER_LEN = 5
TLS_HANDSHAKE_HEADER_LEN = 4
TLS_MAX_RECORD_PAYLOAD = 16384
TLS_MAX_RECORD_SIZE = TLS_RECORD_HEADER_LEN + TLS_MAX_RECORD_PAYLOAD

EXT_SERVER_NAME = 0x0000
EXT_ALPN = 0x0010
EXT_SFB_DATA = 0xFF00

SFB_MAGIC = b'SF'
SFB_VERSION = 0x01
SFB_FLAGS = 0x00

DEFAULT_CIPHER_SUITES = (
    0xC02F,  # TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
    0xC02B,  # TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
    0x009C,  # TLS_RSA_WITH_AES_128_GCM_SHA256
)


def parse_record_header(header, max_record_bytes=None):
    """
    Parse a TLS record header.

    Args:
        header: 5-byte TLS record header
        max_record_bytes: optional on-wire max size (including header)

    Returns:
        int: record payload length

    Raises:
        ValueError: on malformed header
    """
    header = to_bytes(header)
    if len(header) != TLS_RECORD_HEADER_LEN:
        raise ValueError('Invalid TLS record header length')
    content_type, version, length = struct.unpack('!BHH', header)
    if content_type != TLS_CONTENT_TYPE_HANDSHAKE:
        raise ValueError('Invalid TLS content type')
    if version != TLS_VERSION_1_2:
        raise ValueError('Invalid TLS version')
    if length > TLS_MAX_RECORD_PAYLOAD:
        raise ValueError('TLS record too large')
    if max_record_bytes is not None:
        if length + TLS_RECORD_HEADER_LEN > max_record_bytes:
            raise ValueError('TLS record exceeds configured max')
    return length


def parse_client_hello_record(record, max_record_bytes=None):
    """
    Parse a TLS ClientHello record and extract payload.

    Returns:
        tuple: (payload, cipher_suites)
    """
    hs_type, body = _parse_record(record, TLS_HANDSHAKE_CLIENT_HELLO,
                                  max_record_bytes=max_record_bytes)
    return parse_client_hello_body(body)


def parse_server_hello_record(record, max_record_bytes=None):
    """
    Parse a TLS ServerHello record and extract payload.

    Returns:
        tuple: (payload, cipher_suite)
    """
    hs_type, body = _parse_record(record, TLS_HANDSHAKE_SERVER_HELLO,
                                  max_record_bytes=max_record_bytes)
    return parse_server_hello_body(body)


def parse_client_hello_body(body):
    """
    Parse a TLS ClientHello body and extract payload.

    Returns:
        tuple: (payload, cipher_suites)
    """
    body = to_bytes(body)
    offset = 0
    legacy_version, offset = _read_u16(body, offset)
    if legacy_version != TLS_VERSION_1_2:
        raise ValueError('Invalid ClientHello version')
    if offset + 32 > len(body):
        raise ValueError('ClientHello random truncated')
    offset += 32
    session_id_len, offset = _read_u8(body, offset)
    if session_id_len != 0:
        raise ValueError('ClientHello session id not supported')
    if offset + session_id_len > len(body):
        raise ValueError('ClientHello session id truncated')
    offset += session_id_len
    cipher_suites_len, offset = _read_u16(body, offset)
    if cipher_suites_len < 2 or (cipher_suites_len % 2) != 0:
        raise ValueError('ClientHello cipher suite list invalid')
    if offset + cipher_suites_len > len(body):
        raise ValueError('ClientHello cipher suite list truncated')
    cipher_suites = []
    end = offset + cipher_suites_len
    while offset < end:
        cipher_suites.append(_read_u16(body, offset)[0])
        offset += 2
    compression_methods_len, offset = _read_u8(body, offset)
    if compression_methods_len != 1:
        raise ValueError('ClientHello compression list invalid')
    if offset + compression_methods_len > len(body):
        raise ValueError('ClientHello compression list truncated')
    if byte_at(body, offset) != 0x00:
        raise ValueError('ClientHello compression method invalid')
    offset += compression_methods_len
    extensions_len, offset = _read_u16(body, offset)
    if offset + extensions_len != len(body):
        raise ValueError('ClientHello extensions length invalid')
    payload = _parse_extensions(body, offset, len(body))
    return payload, cipher_suites


def parse_server_hello_body(body):
    """
    Parse a TLS ServerHello body and extract payload.

    Returns:
        tuple: (payload, cipher_suite)
    """
    body = to_bytes(body)
    offset = 0
    legacy_version, offset = _read_u16(body, offset)
    if legacy_version != TLS_VERSION_1_2:
        raise ValueError('Invalid ServerHello version')
    if offset + 32 > len(body):
        raise ValueError('ServerHello random truncated')
    offset += 32
    session_id_len, offset = _read_u8(body, offset)
    if session_id_len != 0:
        raise ValueError('ServerHello session id not supported')
    if offset + session_id_len > len(body):
        raise ValueError('ServerHello session id truncated')
    offset += session_id_len
    cipher_suite, offset = _read_u16(body, offset)
    if cipher_suite not in DEFAULT_CIPHER_SUITES:
        raise ValueError('ServerHello cipher suite invalid')
    if offset + 1 > len(body):
        raise ValueError('ServerHello compression method truncated')
    if byte_at(body, offset) != 0x00:
        raise ValueError('ServerHello compression method invalid')
    offset += 1
    extensions_len, offset = _read_u16(body, offset)
    if offset + extensions_len != len(body):
        raise ValueError('ServerHello extensions length invalid')
    payload = _parse_extensions(body, offset, len(body))
    return payload, cipher_suite


def build_client_hello_record(payload, sni=None, alpn_list=None,
                              random_bytes=None,
                              cipher_suites=DEFAULT_CIPHER_SUITES):
    """
    Build a TLS ClientHello record with payload in EXT_SFB_DATA.
    """
    body = build_client_hello_body(payload, sni=sni, alpn_list=alpn_list,
                                   random_bytes=random_bytes,
                                   cipher_suites=cipher_suites)
    return _build_record(TLS_HANDSHAKE_CLIENT_HELLO, body)


def build_server_hello_record(payload, cipher_suite, include_sfb=True,
                              random_bytes=None):
    """
    Build a TLS ServerHello record with payload in EXT_SFB_DATA.
    """
    body = build_server_hello_body(payload, cipher_suite,
                                   include_sfb=include_sfb,
                                   random_bytes=random_bytes)
    return _build_record(TLS_HANDSHAKE_SERVER_HELLO, body)


def build_client_hello_body(payload, sni=None, alpn_list=None,
                            random_bytes=None,
                            cipher_suites=DEFAULT_CIPHER_SUITES):
    payload = to_bytes(payload)
    if random_bytes is None:
        random_bytes = os.urandom(32)
    random_bytes = to_bytes(random_bytes)
    if len(random_bytes) != 32:
        raise ValueError('ClientHello random must be 32 bytes')
    cipher_bytes = _encode_cipher_suites(cipher_suites)
    extensions = _build_extensions(payload, sni=sni, alpn_list=alpn_list)
    body = [
        struct.pack('!H', TLS_VERSION_1_2),
        random_bytes,
        b'\x00',  # session_id_len
        cipher_bytes,
        b'\x01\x00',  # compression_methods_len=1, method=0x00
        struct.pack('!H', len(extensions)),
        extensions,
    ]
    return b''.join(body)


def build_server_hello_body(payload, cipher_suite, include_sfb=True,
                            random_bytes=None):
    payload = to_bytes(payload)
    if cipher_suite not in DEFAULT_CIPHER_SUITES:
        raise ValueError('Unsupported cipher suite')
    if not include_sfb and payload:
        raise ValueError('Payload requires EXT_SFB_DATA')
    if random_bytes is None:
        random_bytes = os.urandom(32)
    random_bytes = to_bytes(random_bytes)
    if len(random_bytes) != 32:
        raise ValueError('ServerHello random must be 32 bytes')
    extensions = b''
    if include_sfb:
        extensions = _build_extensions(payload)
    body = [
        struct.pack('!H', TLS_VERSION_1_2),
        random_bytes,
        b'\x00',  # session_id_len
        struct.pack('!H', cipher_suite),
        b'\x00',  # compression_method
        struct.pack('!H', len(extensions)),
        extensions,
    ]
    return b''.join(body)


def calc_clienthello_payload_cap(max_record_bytes, sni=None, alpn_list=None):
    """
    Calculate max payload bytes for a ClientHello record size cap.
    """
    if max_record_bytes is None or max_record_bytes <= 0:
        return 0
    empty_record = build_client_hello_record(
        b'',
        sni=sni,
        alpn_list=alpn_list,
        random_bytes=b'\x00' * 32,
    )
    overhead = len(empty_record)
    if max_record_bytes < overhead:
        return 0
    return max_record_bytes - overhead


def calc_serverhello_payload_cap(max_record_bytes):
    """
    Calculate max payload bytes for a ServerHello record size cap.
    """
    if max_record_bytes is None or max_record_bytes <= 0:
        return 0
    empty_record = build_server_hello_record(
        b'',
        DEFAULT_CIPHER_SUITES[0],
        include_sfb=True,
        random_bytes=b'\x00' * 32,
    )
    overhead = len(empty_record)
    if max_record_bytes < overhead:
        return 0
    return max_record_bytes - overhead


def build_sni_extension(server_name):
    if not isinstance(server_name, text_type):
        raise TypeError('SNI must be text')
    name_bytes = server_name.encode('ascii')
    entry = b'\x00' + struct.pack('!H', len(name_bytes)) + name_bytes
    data = struct.pack('!H', len(entry)) + entry
    return struct.pack('!HH', EXT_SERVER_NAME, len(data)) + data


def build_alpn_extension(alpn_list):
    if alpn_list is None:
        return b''
    if not isinstance(alpn_list, (list, tuple)):
        raise TypeError('ALPN list must be list or tuple')
    parts = []
    total = 0
    for proto in alpn_list:
        if not isinstance(proto, text_type):
            raise TypeError('ALPN must be text')
        proto_bytes = proto.encode('ascii')
        if not proto_bytes:
            raise ValueError('ALPN entry empty')
        if len(proto_bytes) > 255:
            raise ValueError('ALPN entry too long')
        parts.append(struct.pack('!B', len(proto_bytes)) + proto_bytes)
        total += 1 + len(proto_bytes)
    data = struct.pack('!H', total) + b''.join(parts)
    return struct.pack('!HH', EXT_ALPN, len(data)) + data


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


def _build_record(handshake_type, body):
    body = to_bytes(body)
    if len(body) > 0xFFFFFF:
        raise ValueError('Handshake body too large')
    handshake = (
        struct.pack('!B', handshake_type) +
        _pack_u24(len(body)) +
        body
    )
    if len(handshake) > TLS_MAX_RECORD_PAYLOAD:
        raise ValueError('Handshake record too large')
    record = (
        struct.pack('!BHH', TLS_CONTENT_TYPE_HANDSHAKE,
                    TLS_VERSION_1_2, len(handshake)) +
        handshake
    )
    return record


def _build_extensions(payload, sni=None, alpn_list=None):
    extensions = []
    if sni is not None:
        extensions.append(build_sni_extension(sni))
    if alpn_list:
        extensions.append(build_alpn_extension(alpn_list))
    extensions.append(_build_sfb_extension(payload))
    return b''.join(extensions)


def _build_sfb_extension(payload):
    payload = to_bytes(payload)
    if len(payload) > 0xFFFF:
        raise ValueError('Payload too large for EXT_SFB_DATA')
    data = (
        SFB_MAGIC +
        struct.pack('!B', SFB_VERSION) +
        struct.pack('!B', SFB_FLAGS) +
        struct.pack('!H', len(payload)) +
        payload
    )
    return struct.pack('!HH', EXT_SFB_DATA, len(data)) + data


def _parse_extensions(data, offset, end):
    payload = None
    sfb_seen = False
    while offset < end:
        if offset + 4 > end:
            raise ValueError('Extension header truncated')
        ext_type = struct.unpack('!H', data[offset:offset + 2])[0]
        ext_len = struct.unpack('!H', data[offset + 2:offset + 4])[0]
        offset += 4
        if offset + ext_len > end:
            raise ValueError('Extension data truncated')
        ext_data = data[offset:offset + ext_len]
        if ext_type == EXT_SFB_DATA:
            if sfb_seen:
                raise ValueError('Duplicate EXT_SFB_DATA')
            payload = _parse_sfb_extension(ext_data)
            sfb_seen = True
        offset += ext_len
    if not sfb_seen:
        raise ValueError('Missing EXT_SFB_DATA')
    return payload


def _parse_sfb_extension(data):
    data = to_bytes(data)
    if len(data) < 6:
        raise ValueError('EXT_SFB_DATA truncated')
    if data[:2] != SFB_MAGIC:
        raise ValueError('EXT_SFB_DATA magic invalid')
    version = byte_at(data, 2)
    if version != SFB_VERSION:
        raise ValueError('EXT_SFB_DATA version invalid')
    flags = byte_at(data, 3)
    if flags != SFB_FLAGS:
        raise ValueError('EXT_SFB_DATA flags invalid')
    payload_len = struct.unpack('!H', data[4:6])[0]
    if payload_len != len(data) - 6:
        raise ValueError('EXT_SFB_DATA length mismatch')
    return data[6:]


def _encode_cipher_suites(cipher_suites):
    if not cipher_suites:
        raise ValueError('Cipher suite list required')
    parts = []
    for cipher in cipher_suites:
        parts.append(struct.pack('!H', int(cipher) & 0xFFFF))
    suite_bytes = b''.join(parts)
    return struct.pack('!H', len(suite_bytes)) + suite_bytes


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
