# -*- coding: ascii -*-
"""Tests for TLS ClientHello codec."""

from __future__ import absolute_import

import struct
import unittest

from sfb.transport.tls import codec
from sfb.compat import to_bytes


def _build_clienthello_with_extensions(extensions):
    cipher_bytes = codec._encode_cipher_suites(codec.DEFAULT_CIPHER_SUITES)
    body = b''.join([
        struct.pack('!H', codec.TLS_VERSION_1_2),
        b'\x00' * 32,
        b'\x00',  # session_id_len
        cipher_bytes,
        b'\x01\x00',  # compression_methods_len=1, method=0x00
        struct.pack('!H', len(extensions)),
        extensions,
    ])
    return codec._build_record(codec.TLS_HANDSHAKE_CLIENT_HELLO, body)


def _build_serverhello_with_extensions(extensions, cipher_suite):
    body = b''.join([
        struct.pack('!H', codec.TLS_VERSION_1_2),
        b'\x00' * 32,
        b'\x00',  # session_id_len
        struct.pack('!H', cipher_suite),
        b'\x00',  # compression_method
        struct.pack('!H', len(extensions)),
        extensions,
    ])
    return codec._build_record(codec.TLS_HANDSHAKE_SERVER_HELLO, body)


class TlsCodecTests(unittest.TestCase):
    def test_clienthello_roundtrip(self):
        payload = b'hello'
        record = codec.build_client_hello_record(payload, random_bytes=b'\x01' * 32)
        parsed, suites = codec.parse_client_hello_record(record)
        self.assertEqual(parsed, payload)
        self.assertEqual(suites, list(codec.DEFAULT_CIPHER_SUITES))

    def test_serverhello_roundtrip(self):
        payload = b'world'
        record = codec.build_server_hello_record(
            payload,
            codec.DEFAULT_CIPHER_SUITES[0],
            random_bytes=b'\x02' * 32,
        )
        parsed, cipher = codec.parse_server_hello_record(record)
        self.assertEqual(parsed, payload)
        self.assertEqual(cipher, codec.DEFAULT_CIPHER_SUITES[0])

    def test_record_header_validation(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        bad[0] = 0x15
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))
        bad = bytearray(record)
        bad[1] = 0x03
        bad[2] = 0x04
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_record_length_mismatch(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        length = struct.unpack('!H', bad[3:5])[0]
        bad[3:5] = struct.pack('!H', length - 1)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_session_id_len_rejected(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        bad[43] = 1
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_cipher_suites_len_rejected(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        bad[44:46] = struct.pack('!H', 3)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_compression_methods_rejected(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        bad[52] = 2
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))
        bad = bytearray(record)
        bad[53] = 1
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_missing_sfb_extension(self):
        record = codec.build_client_hello_record(b'a', random_bytes=b'\x00' * 32)
        bad = bytearray(record)
        bad[56:58] = struct.pack('!H', 0xFF01)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(to_bytes(bad))

    def test_duplicate_sfb_extension(self):
        ext = codec._build_sfb_extension(b'abc')
        record = _build_clienthello_with_extensions(ext + ext)
        with self.assertRaises(ValueError):
            codec.parse_client_hello_record(record)

    def test_unknown_extension_ignored(self):
        unknown = struct.pack('!HH', 0x1234, 1) + b'x'
        ext = codec._build_sfb_extension(b'abc')
        record = _build_clienthello_with_extensions(unknown + ext)
        payload, _ = codec.parse_client_hello_record(record)
        self.assertEqual(payload, b'abc')

    def test_serverhello_cipher_suite_mismatch(self):
        ext = codec._build_sfb_extension(b'abc')
        record = _build_serverhello_with_extensions(ext, 0xFFFF)
        with self.assertRaises(ValueError):
            codec.parse_server_hello_record(record)

    def test_record_length_bounds(self):
        header = struct.pack('!BHH', codec.TLS_CONTENT_TYPE_HANDSHAKE,
                             codec.TLS_VERSION_1_2, 0x4001)
        with self.assertRaises(ValueError):
            codec.parse_record_header(header)


if __name__ == '__main__':
    unittest.main()
