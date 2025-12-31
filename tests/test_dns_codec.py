# -*- coding: ascii -*-
"""Tests for DNS codec."""

from __future__ import absolute_import

import unittest

from sfb.transport.dns import dns_codec as codec


class TestBase32(unittest.TestCase):
    """Tests for base32 encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        encoded = codec.base32_encode(data)
        decoded = codec.base32_decode(encoded)
        self.assertEqual(decoded, data)

    def test_encode_no_padding(self):
        # Base32 should not have padding
        encoded = codec.base32_encode(b'Hello')
        self.assertNotIn('=', encoded)

    def test_encode_uppercase(self):
        encoded = codec.base32_encode(b'test')
        self.assertEqual(encoded, encoded.upper())

    def test_decode_case_insensitive(self):
        data = b'Hello'
        encoded = codec.base32_encode(data)
        self.assertEqual(codec.base32_decode(encoded.lower()), data)
        self.assertEqual(codec.base32_decode(encoded.upper()), data)

    def test_empty(self):
        self.assertEqual(codec.base32_encode(b''), '')
        self.assertEqual(codec.base32_decode(''), b'')

    def test_known_vectors(self):
        # RFC 4648 test vectors
        self.assertEqual(codec.base32_encode(b'f'), 'MY')
        self.assertEqual(codec.base32_encode(b'fo'), 'MZXQ')
        self.assertEqual(codec.base32_encode(b'foo'), 'MZXW6')
        self.assertEqual(codec.base32_encode(b'foob'), 'MZXW6YQ')
        self.assertEqual(codec.base32_encode(b'fooba'), 'MZXW6YTB')
        self.assertEqual(codec.base32_encode(b'foobar'), 'MZXW6YTBOI')


class TestBase64(unittest.TestCase):
    """Tests for base64 encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        encoded = codec.base64_encode(data)
        decoded = codec.base64_decode(encoded)
        self.assertEqual(decoded, data)

    def test_encode_no_padding(self):
        encoded = codec.base64_encode(b'Hello')
        self.assertNotIn('=', encoded)

    def test_empty(self):
        self.assertEqual(codec.base64_encode(b''), '')
        self.assertEqual(codec.base64_decode(''), b'')

    def test_known_vectors(self):
        self.assertEqual(codec.base64_encode(b'Hello'), 'SGVsbG8')
        self.assertEqual(codec.base64_decode('SGVsbG8'), b'Hello')


class TestDnsName(unittest.TestCase):
    """Tests for DNS name encoding/decoding."""

    def test_encode_simple(self):
        wire = codec.encode_name('example.com')
        self.assertEqual(wire, b'\x07example\x03com\x00')

    def test_encode_subdomain(self):
        wire = codec.encode_name('sub.example.com')
        self.assertEqual(wire, b'\x03sub\x07example\x03com\x00')

    def test_decode_simple(self):
        wire = b'\x07example\x03com\x00'
        name, offset = codec.decode_name(wire, 0)
        self.assertEqual(name, 'example.com')
        self.assertEqual(offset, len(wire))

    def test_decode_with_offset(self):
        wire = b'\x00\x00\x07example\x03com\x00'
        name, offset = codec.decode_name(wire, 2)
        self.assertEqual(name, 'example.com')

    def test_decode_compression(self):
        # Compression pointer: 0xC0 | offset
        wire = b'\x07example\x03com\x00\x03sub\xc0\x00'
        # First name at offset 0
        name1, off1 = codec.decode_name(wire, 0)
        self.assertEqual(name1, 'example.com')
        self.assertEqual(off1, 13)
        # Second name at offset 13 with compression
        name2, off2 = codec.decode_name(wire, 13)
        self.assertEqual(name2, 'sub.example.com')
        self.assertEqual(off2, 19)

    def test_decode_rejects_compression_when_disallowed(self):
        wire = b'\x07example\x03com\x00\x03sub\xc0\x00'
        with self.assertRaises(ValueError):
            codec.decode_name(wire, 13, allow_compression=False)

    def test_skip_name(self):
        wire = b'\x07example\x03com\x00\xff\xff'
        offset = codec.skip_name(wire, 0)
        self.assertEqual(offset, 13)

    def test_skip_name_compression(self):
        wire = b'\x03sub\xc0\x00\xff\xff'
        offset = codec.skip_name(wire, 0)
        self.assertEqual(offset, 6)


class TestQueryName(unittest.TestCase):
    """Tests for query name encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        base_domain = 'tunnel.example.com'
        encoded = codec.encode_query_name(data, base_domain, 1234)
        decoded = codec.decode_query_name(encoded, base_domain)
        self.assertEqual(decoded, data)

    def test_nonce_prefix(self):
        data = b'test'
        encoded = codec.encode_query_name(data, 'example.com', 0)
        labels = encoded.split('.')
        # First label should be nonce (4 chars)
        self.assertEqual(len(labels[0]), 4)

    def test_different_nonces(self):
        data = b'test'
        enc1 = codec.encode_query_name(data, 'example.com', 1)
        enc2 = codec.encode_query_name(data, 'example.com', 2)
        # Same data, different nonces -> different query names
        self.assertNotEqual(enc1, enc2)
        # But decode to same data
        self.assertEqual(codec.decode_query_name(enc1, 'example.com'), data)
        self.assertEqual(codec.decode_query_name(enc2, 'example.com'), data)

    def test_label_splitting(self):
        # Large data should be split across labels
        data = b'x' * 100  # Will be 160 base32 chars
        encoded = codec.encode_query_name(data, 'example.com', 0)
        labels = encoded.split('.')
        # Check no label exceeds default limit
        for label in labels:
            self.assertLessEqual(len(label), codec.DEFAULT_LABEL_MAX_LEN)

    def test_label_splitting_custom_limit(self):
        data = b'x' * 100
        encoded = codec.encode_query_name(
            data, 'example.com', 0, label_max_len=40
        )
        labels = encoded.split('.')
        for label in labels:
            self.assertLessEqual(len(label), 40)

    def test_empty_data(self):
        encoded = codec.encode_query_name(b'', 'example.com', 0)
        with self.assertRaises(ValueError):
            codec.decode_query_name(encoded, 'example.com')

    def test_wrong_base_domain(self):
        encoded = codec.encode_query_name(b'test', 'example.com', 0)
        with self.assertRaises(ValueError):
            codec.decode_query_name(encoded, 'other.com')

    def test_case_insensitive(self):
        data = b'test'
        encoded = codec.encode_query_name(data, 'example.com', 0)
        # DNS is case-insensitive
        decoded = codec.decode_query_name(encoded.upper(), 'EXAMPLE.COM')
        self.assertEqual(decoded, data)


class TestTxtRdata(unittest.TestCase):
    """Tests for TXT record RDATA encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        rdata = codec.encode_txt_rdata(data)
        decoded = codec.decode_txt_rdata(rdata)
        self.assertEqual(decoded, data)

    def test_length_prefix(self):
        data = b'Hello'
        rdata = codec.encode_txt_rdata(data)
        # First byte is length
        length = rdata[0] if isinstance(rdata[0], int) else ord(rdata[0])
        self.assertGreater(length, 0)
        self.assertLessEqual(length, 255)

    def test_empty(self):
        rdata = codec.encode_txt_rdata(b'')
        decoded = codec.decode_txt_rdata(rdata)
        self.assertEqual(decoded, b'')

    def test_large_data_multiple_strings(self):
        # Data that encodes to more than 255 chars should split
        data = b'x' * 200  # Will be ~267 base64 chars
        rdata = codec.encode_txt_rdata(data)
        decoded = codec.decode_txt_rdata(rdata)
        self.assertEqual(decoded, data)

    def test_truncated_string(self):
        # Malformed: length says 10 but only 5 bytes
        rdata = b'\x0aHello'
        with self.assertRaises(ValueError):
            codec.decode_txt_rdata(rdata)


class TestCnameTarget(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        suffix = 'c.example.com'
        encoded = codec.encode_cname_target(data, suffix)
        decoded = codec.decode_cname_target(encoded, suffix)
        self.assertEqual(decoded, data)

    def test_empty_data(self):
        encoded = codec.encode_cname_target(b'', 'c.example.com')
        with self.assertRaises(ValueError):
            codec.decode_cname_target(encoded, 'c.example.com')

    def test_wrong_suffix(self):
        encoded = codec.encode_cname_target(b'test', 'c.example.com')
        with self.assertRaises(ValueError):
            codec.decode_cname_target(encoded, 'x.example.com')


class TestMtuCalculation(unittest.TestCase):
    """Tests for MTU calculation."""

    def test_query_mtu_short_domain(self):
        mtu = codec.calc_query_mtu('x.co')
        self.assertGreater(mtu, 140)

    def test_query_mtu_long_domain(self):
        mtu = codec.calc_query_mtu('very.long.subdomain.example.com')
        self.assertGreater(mtu, 100)
        self.assertLess(mtu, 150)

    def test_query_mtu_decreases_with_domain_length(self):
        mtu_short = codec.calc_query_mtu('x.co')
        mtu_long = codec.calc_query_mtu('tunnel.example.com')
        self.assertGreater(mtu_short, mtu_long)

    def test_response_mtu_standard(self):
        mtu = codec.calc_response_mtu(codec.QTYPE_TXT, 512)
        self.assertEqual(mtu, 191)  # floor(255 * 3 / 4)

    def test_response_mtu_edns(self):
        mtu = codec.calc_response_mtu(codec.QTYPE_TXT, 4096)
        self.assertGreater(mtu, 2000)

    def test_response_mtu_increases_with_edns(self):
        mtu_std = codec.calc_response_mtu(codec.QTYPE_TXT, 512)
        mtu_edns = codec.calc_response_mtu(codec.QTYPE_TXT, 4096)
        self.assertGreater(mtu_edns, mtu_std)

    def test_response_mtu_cname(self):
        mtu = codec.calc_response_mtu(
            codec.QTYPE_CNAME, 512, 'c.example.com'
        )
        self.assertGreater(mtu, 100)


if __name__ == '__main__':
    unittest.main()
