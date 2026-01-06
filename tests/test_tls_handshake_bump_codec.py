# -*- coding: ascii -*-
"""Tests for TLS handshake bump codec."""

from __future__ import absolute_import

import struct
import unittest

from sfb.transport.tls_handshake_bump import tls_handshake_bump_codec as codec
from sfb.transport.tls_handshake_bump import (
    tls_handshake_bump_cert_template as cert_template,
)


def _pack_u24(value):
    return struct.pack('!I', value)[1:]


def _build_clienthello_record_with_sni(sni_name):
    sni_bytes = sni_name.encode('ascii')
    name_entry = b'\x00' + struct.pack('!H', len(sni_bytes)) + sni_bytes
    sni_list = struct.pack('!H', len(name_entry)) + name_entry
    sni_ext = struct.pack('!HH', codec.EXT_SERVER_NAME, len(sni_list)) + sni_list
    extensions = sni_ext
    body = b''.join([
        struct.pack('!H', codec.TLS_VERSION_1_2),
        b'\x00' * 32,
        b'\x00',  # session_id_len
        struct.pack('!H', 2) + struct.pack('!H', codec.TLS_RSA_WITH_AES_128_CBC_SHA),
        b'\x01\x00',  # compression_methods_len=1, method=0x00
        struct.pack('!H', len(extensions)),
        extensions,
    ])
    handshake = (
        struct.pack('!B', codec.TLS_HANDSHAKE_CLIENT_HELLO) +
        _pack_u24(len(body)) +
        body
    )
    return (
        struct.pack('!BHH', codec.TLS_CONTENT_TYPE_HANDSHAKE,
                    codec.TLS_VERSION_1_2, len(handshake)) +
        handshake
    )


def _build_clienthello_record(body):
    handshake = (
        struct.pack('!B', codec.TLS_HANDSHAKE_CLIENT_HELLO) +
        _pack_u24(len(body)) +
        body
    )
    return (
        struct.pack('!BHH', codec.TLS_CONTENT_TYPE_HANDSHAKE,
                    codec.TLS_VERSION_1_2, len(handshake)) +
        handshake
    )


def _build_clienthello_body_with_extensions(extensions):
    return b''.join([
        struct.pack('!H', codec.TLS_VERSION_1_2),
        b'\x00' * 32,
        b'\x00',  # session_id_len
        struct.pack('!H', 2) + struct.pack('!H', codec.TLS_RSA_WITH_AES_128_CBC_SHA),
        b'\x01\x00',  # compression_methods_len=1, method=0x00
        struct.pack('!H', len(extensions)),
        extensions,
    ])


class TlsHandshakeBumpCodecTests(unittest.TestCase):
    def test_parse_record_header_invalid_length(self):
        with self.assertRaises(ValueError):
            codec.parse_record_header(b'\x16\x03\x03\x00')
        with self.assertRaises(ValueError):
            codec.parse_record_header(b'\x16\x03\x03\x00\x00\x00')

    def test_parse_record_header_invalid_content_type(self):
        header = struct.pack('!BHH', 0x15, codec.TLS_VERSION_1_2, 0)
        with self.assertRaises(ValueError):
            codec.parse_record_header(header)

    def test_parse_record_header_invalid_version(self):
        for version in (0x0300, 0x0304):
            header = struct.pack(
                '!BHH',
                codec.TLS_CONTENT_TYPE_HANDSHAKE,
                version,
                0,
            )
            with self.assertRaises(ValueError):
                codec.parse_record_header(header)

    def test_parse_record_header_payload_too_large(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            codec.TLS_MAX_RECORD_PAYLOAD + 1,
        )
        with self.assertRaises(ValueError):
            codec.parse_record_header(header)

    def test_parse_record_header_max_record_bytes(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            1,
        )
        with self.assertRaises(ValueError):
            codec.parse_record_header(
                header,
                max_record_bytes=codec.TLS_RECORD_HEADER_LEN,
            )

    def test_parse_client_hello_sni_record_length_mismatch(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            1,
        )
        record = header
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_sni_max_record_bytes(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            1,
        )
        record = header + b'\x00'
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(
                record,
                max_record_bytes=codec.TLS_RECORD_HEADER_LEN,
            )

    def test_parse_client_hello_sni_from_buffer_record_length_mismatch(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            1,
        )
        record = header
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, 1)

    def test_parse_client_hello_sni_from_buffer_max_record_bytes(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            1,
        )
        record = bytearray(header + b'\x00')
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(
                record,
                1,
                max_record_bytes=codec.TLS_RECORD_HEADER_LEN,
            )

    def test_base32_roundtrip(self):
        data = b'hello'
        encoded = codec.base32_encode(data)
        self.assertEqual(encoded, encoded.lower())
        self.assertNotIn('=', encoded)
        decoded = codec.base32_decode(encoded)
        self.assertEqual(decoded, data)

    def test_sni_roundtrip(self):
        payload = b'ping'
        base_domain = 'example.com'
        sni = codec.encode_sni_name(payload, base_domain)
        decoded = codec.decode_sni_name(sni, base_domain)
        self.assertEqual(decoded, payload)

    def test_sni_domain_mismatch(self):
        payload = b'ping'
        sni = codec.encode_sni_name(payload, 'example.com')
        with self.assertRaises(ValueError):
            codec.decode_sni_name(sni, 'other.com')

    def test_sni_base_domain_non_text(self):
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', b'example.com')

    def test_sni_base_domain_non_ascii(self):
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', u'exampl\u00e9.com')

    def test_sni_base_domain_empty(self):
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', '')

    def test_sni_base_domain_invalid_label(self):
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', 'example..com')

    def test_sni_base_domain_label_too_long(self):
        label = 'a' * (codec.MAX_LABEL_LEN + 1)
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', label + '.com')

    def test_sni_base_domain_name_too_long(self):
        label = 'a' * codec.MAX_LABEL_LEN
        base_domain = '.'.join([label] * 4)
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'ping', base_domain)

    def test_sni_payload_missing_exact_domain(self):
        with self.assertRaises(ValueError):
            codec.decode_sni_name('example.com', 'example.com')

    def test_sni_payload_missing_empty_prefix(self):
        with self.assertRaises(ValueError):
            codec.decode_sni_name('.example.com', 'example.com')

    def test_sni_payload_missing_trailing_dot_prefix(self):
        with self.assertRaises(ValueError):
            codec.decode_sni_name('a..example.com', 'example.com')

    def test_sni_name_non_text(self):
        with self.assertRaises(TypeError):
            codec.decode_sni_name(b'abc.example.com', 'example.com')

    def test_sni_name_non_ascii(self):
        with self.assertRaises(ValueError):
            codec.decode_sni_name(u'exampl\u00e9.example.com', 'example.com')

    def test_cn_roundtrip(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=128)
        decoded = codec.decode_cn_value(cn)
        self.assertEqual(decoded, payload)

    def test_cn_decode_non_text(self):
        with self.assertRaises(TypeError):
            codec.decode_cn_value(b'abcd')

    def test_cn_padded_roundtrip(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=cert_template.CN_LEN)
        padded = cn + ('a' * (cert_template.CN_LEN - len(cn)))
        decoded = codec.decode_cn_value(padded)
        self.assertEqual(decoded, payload)

    def test_scan_response_payload(self):
        payload = b'pingpong'
        cn = codec.encode_cn_value(payload, max_len=cert_template.CN_LEN)
        padded = cn + ('a' * (cert_template.CN_LEN - len(cn)))
        buffer_bytes = b'xx' + padded.encode('ascii') + b'yy'
        decoded = codec.scan_response_payload(
            buffer_bytes,
            max_payload_len=64,
            max_token_len=cert_template.CN_LEN,
        )
        self.assertEqual(decoded, payload)

    def test_scan_response_payload_start_offset(self):
        payload = b'pingpong'
        cn = codec.encode_cn_value(payload, max_len=cert_template.CN_LEN)
        buffer_bytes = b'xx' + cn.encode('ascii')
        decoded = codec.scan_response_payload(
            buffer_bytes,
            start_offset=2,
        )
        self.assertEqual(decoded, payload)
        self.assertIsNone(codec.scan_response_payload(buffer_bytes, start_offset=999))
        decoded = codec.scan_response_payload(buffer_bytes, start_offset=-1)
        self.assertEqual(decoded, payload)

    def test_scan_response_payload_max_token_len_too_small(self):
        payload = b'pingpong'
        cn = codec.encode_cn_value(payload, max_len=cert_template.CN_LEN)
        buffer_bytes = cn.encode('ascii')
        decoded = codec.scan_response_payload(
            buffer_bytes,
            max_token_len=codec.SFB_BUMP_RESPONSE_TOKEN_MIN_LEN - 1,
        )
        self.assertIsNone(decoded)

    def test_response_checksum_reject(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=128)
        if cn[0] != 'a':
            tampered = 'a' + cn[1:]
        else:
            tampered = 'b' + cn[1:]
        with self.assertRaises(ValueError):
            codec.decode_cn_value(tampered)
        scanned = codec.scan_response_payload(tampered.encode('ascii'))
        self.assertIsNone(scanned)

    def test_response_padding_mismatch(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=128)
        decoded = codec.base32_decode(cn)
        payload_len = struct.unpack('!H', decoded[1:3])[0]
        tampered = (
            decoded[:1] +
            struct.pack('!H', payload_len - 1) +
            decoded[3:5] +
            decoded[5:]
        )
        encoded = codec.base32_encode(tampered)
        with self.assertRaises(ValueError):
            codec.decode_cn_value(encoded)

    def test_response_payload_len_too_large(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=128)
        with self.assertRaises(ValueError):
            codec._decode_response_payload(cn, max_payload_len=1)

    def test_response_token_invalid_header_bytes(self):
        token = b'!' + (b'A' * (codec.SFB_BUMP_RESPONSE_TOKEN_MIN_LEN - 1))
        self.assertIsNone(codec._try_decode_response_header_at(token, 0))

    def test_sni_payload_cap(self):
        base_domain = 'example.com'
        cap = codec.calc_sni_payload_cap(base_domain)
        self.assertGreaterEqual(cap, 1)
        codec.encode_sni_name(b'a' * cap, base_domain)
        with self.assertRaises(ValueError):
            codec.encode_sni_name(b'a' * (cap + 1), base_domain)

    def test_cn_payload_cap(self):
        cn_max_len = 32
        cap = codec.calc_cn_payload_cap(cn_max_len)
        self.assertGreaterEqual(cap, 1)
        codec.encode_cn_value(b'a' * cap, max_len=cn_max_len)
        with self.assertRaises(ValueError):
            codec.encode_cn_value(b'a' * (cap + 1), max_len=cn_max_len)

    def test_parse_client_hello_sni(self):
        sni = 'test.example.com'
        record = _build_clienthello_record_with_sni(sni)
        parsed = codec.parse_client_hello_sni(record)
        self.assertEqual(parsed, sni)

    def test_parse_client_hello_sni_from_buffer(self):
        sni = 'test.example.com'
        record = _build_clienthello_record_with_sni(sni)
        record_len = codec.parse_record_header(record[:codec.TLS_RECORD_HEADER_LEN])
        parsed = codec.parse_client_hello_sni_from_buffer(bytearray(record), record_len)
        self.assertEqual(parsed, sni)

    def test_parse_client_hello_sni_record_too_short(self):
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(b'\x16\x03\x03')

    def test_parse_client_hello_sni_from_buffer_invalid_record_len(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            0,
        )
        record = header
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, None)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, -1)

    def test_parse_client_hello_sni_from_buffer_handshake_header_truncated(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            2,
        )
        record = header + b'\x00\x00'
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, 2)

    def test_parse_client_hello_sni_from_buffer_invalid_handshake_type(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            codec.TLS_HANDSHAKE_HEADER_LEN,
        )
        handshake = (
            struct.pack('!B', codec.TLS_HANDSHAKE_SERVER_HELLO) +
            _pack_u24(0)
        )
        record = header + handshake
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, len(handshake))

    def test_parse_client_hello_sni_from_buffer_handshake_length_mismatch(self):
        header = struct.pack(
            '!BHH',
            codec.TLS_CONTENT_TYPE_HANDSHAKE,
            codec.TLS_VERSION_1_2,
            codec.TLS_HANDSHAKE_HEADER_LEN,
        )
        handshake = (
            struct.pack('!B', codec.TLS_HANDSHAKE_CLIENT_HELLO) +
            _pack_u24(1)
        )
        record = header + handshake
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni_from_buffer(record, len(handshake))

    def test_parse_client_hello_invalid_legacy_version(self):
        body = struct.pack('!H', 0x0200)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_random_truncated(self):
        body = struct.pack('!H', codec.TLS_VERSION_1_2) + (b'\x00' * 10)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_session_id_truncated(self):
        body = (
            struct.pack('!H', codec.TLS_VERSION_1_2) +
            (b'\x00' * 32) +
            b'\x01'
        )
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_cipher_suites_truncated(self):
        body = (
            struct.pack('!H', codec.TLS_VERSION_1_2) +
            (b'\x00' * 32) +
            b'\x00' +
            struct.pack('!H', 2)
        )
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_compression_list_truncated(self):
        body = (
            struct.pack('!H', codec.TLS_VERSION_1_2) +
            (b'\x00' * 32) +
            b'\x00' +
            struct.pack('!H', 2) +
            struct.pack('!H', codec.TLS_RSA_WITH_AES_128_CBC_SHA) +
            b'\x02'
        )
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_extensions_length_mismatch(self):
        body = (
            struct.pack('!H', codec.TLS_VERSION_1_2) +
            (b'\x00' * 32) +
            b'\x00' +
            struct.pack('!H', 2) +
            struct.pack('!H', codec.TLS_RSA_WITH_AES_128_CBC_SHA) +
            b'\x01\x00' +
            struct.pack('!H', 1)
        )
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_missing_sni_extension(self):
        extensions = struct.pack('!HH', 0x000a, 0)
        body = _build_clienthello_body_with_extensions(extensions)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_sni_list_length_invalid(self):
        sni_data = struct.pack('!H', 2) + b'\x00'
        extensions = struct.pack(
            '!HH',
            codec.EXT_SERVER_NAME,
            len(sni_data),
        ) + sni_data
        body = _build_clienthello_body_with_extensions(extensions)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_sni_entry_truncated(self):
        sni_data = struct.pack('!H', 1) + b'\x00'
        extensions = struct.pack(
            '!HH',
            codec.EXT_SERVER_NAME,
            len(sni_data),
        ) + sni_data
        body = _build_clienthello_body_with_extensions(extensions)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_parse_client_hello_sni_non_ascii_name(self):
        sni_entry = b'\x00' + struct.pack('!H', 1) + b'\xff'
        sni_data = struct.pack('!H', len(sni_entry)) + sni_entry
        extensions = struct.pack(
            '!HH',
            codec.EXT_SERVER_NAME,
            len(sni_data),
        ) + sni_data
        body = _build_clienthello_body_with_extensions(extensions)
        record = _build_clienthello_record(body)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_sni(record)

    def test_build_server_handshake_record_random_len(self):
        with self.assertRaises(ValueError):
            codec.build_server_handshake_record(b'\x00', random_bytes=b'\x00')

    def test_build_server_handshake_record_too_large(self):
        oversize = b'\x00' * (codec.TLS_MAX_RECORD_PAYLOAD + 1)
        with self.assertRaises(ValueError):
            codec.build_server_handshake_record(oversize)

    def test_build_server_handshake_record_structure(self):
        cert_der = b'\x01\x02\x03\x04'
        random_bytes = b'\x11' * 32
        record = codec.build_server_handshake_record(
            cert_der,
            random_bytes=random_bytes,
        )
        length = codec.parse_record_header(
            record[:codec.TLS_RECORD_HEADER_LEN]
        )
        self.assertEqual(len(record), codec.TLS_RECORD_HEADER_LEN + length)
        offset = codec.TLS_RECORD_HEADER_LEN
        self.assertEqual(
            record[offset],
            codec.TLS_HANDSHAKE_SERVER_HELLO,
        )


if __name__ == '__main__':
    unittest.main()
