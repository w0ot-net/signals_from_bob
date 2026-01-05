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


class TlsHandshakeBumpCodecTests(unittest.TestCase):
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

    def test_cn_roundtrip(self):
        payload = b'pong'
        cn = codec.encode_cn_value(payload, max_len=128)
        decoded = codec.decode_cn_value(cn)
        self.assertEqual(decoded, payload)

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


if __name__ == '__main__':
    unittest.main()
