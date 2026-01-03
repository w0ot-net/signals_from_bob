# -*- coding: ascii -*-
"""Tests for DNS codec."""

from __future__ import absolute_import

import binascii
import struct
import unittest

from sfb.transport.dns import codec


class TestBase32(unittest.TestCase):
    """Tests for base32 encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        encoded = codec.base32_encode(data)
        decoded = codec.base32_decode(encoded)
        self.assertEqual(decoded, data)

    def test_encode_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.base32_encode(u'text')

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

    def test_decode_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.base32_decode(b'AAAA')

    def test_decode_invalid_chars(self):
        with self.assertRaises(binascii.Error):
            codec.base32_decode('!@#$')

    def test_decode_invalid_length(self):
        with self.assertRaises(binascii.Error):
            codec.base32_decode('A')

    def test_decode_rejects_non_ascii_text(self):
        with self.assertRaises(UnicodeEncodeError):
            codec.base32_decode(u'\u00ff')

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

    def test_encode_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.base64_encode(u'text')

    def test_encode_no_padding(self):
        encoded = codec.base64_encode(b'Hello')
        self.assertNotIn('=', encoded)

    def test_empty(self):
        self.assertEqual(codec.base64_encode(b''), '')
        self.assertEqual(codec.base64_decode(''), b'')

    def test_decode_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.base64_decode(b'AAAA')

    def test_decode_invalid_length(self):
        with self.assertRaises(binascii.Error):
            codec.base64_decode('A')

    def test_decode_invalid_chars(self):
        self.assertEqual(codec.base64_decode(u'!!!!'), b'')

    def test_decode_rejects_non_ascii_text(self):
        with self.assertRaises(UnicodeEncodeError):
            codec.base64_decode(u'\u00ff')

    def test_known_vectors(self):
        self.assertEqual(codec.base64_encode(b'Hello'), 'SGVsbG8')
        self.assertEqual(codec.base64_decode('SGVsbG8'), b'Hello')


class TestDnsName(unittest.TestCase):
    """Tests for DNS name encoding/decoding."""

    def test_encode_simple(self):
        wire = codec.encode_name('example.com')
        self.assertEqual(wire, b'\x07example\x03com\x00')

    def test_encode_root(self):
        self.assertEqual(codec.encode_name('.'), b'\x00')

    def test_encode_empty_string(self):
        self.assertEqual(codec.encode_name(u''), b'\x00')

    def test_encode_trailing_dot(self):
        wire = codec.encode_name('example.com.')
        self.assertEqual(wire, b'\x07example\x03com\x00')

    def test_encode_consecutive_dots_normalized(self):
        self.assertEqual(codec.encode_name('a..b'), codec.encode_name('a.b'))

    def test_encode_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.encode_name(b'example.com')

    def test_encode_non_ascii_label(self):
        with self.assertRaises(UnicodeEncodeError):
            codec.encode_name(u'\u00ff.com')

    def test_encode_label_max_len(self):
        name = '%s.com' % ('a' * 63)
        wire = codec.encode_name(name)
        decoded, offset = codec.decode_name(wire, 0)
        self.assertEqual(decoded, name)
        self.assertEqual(offset, len(wire))

    def test_encode_label_too_long(self):
        with self.assertRaises(ValueError):
            codec.encode_name('%s.com' % ('a' * 64))

    def test_encode_name_too_long(self):
        too_long = '.'.join(['a' * 63] * 4)
        with self.assertRaises(ValueError):
            codec.encode_name(too_long)

    def test_encode_name_max_len(self):
        labels = ['a' * 63, 'b' * 63, 'c' * 63, 'd' * 61]
        name = '.'.join(labels)
        self.assertEqual(len(name), codec.MAX_NAME_LEN)
        wire = codec.encode_name(name)
        decoded, offset = codec.decode_name(wire, 0)
        self.assertEqual(decoded, name)
        self.assertEqual(offset, len(wire))

    def test_encode_subdomain(self):
        wire = codec.encode_name('sub.example.com')
        self.assertEqual(wire, b'\x03sub\x07example\x03com\x00')

    def test_decode_simple(self):
        wire = b'\x07example\x03com\x00'
        name, offset = codec.decode_name(wire, 0)
        self.assertEqual(name, 'example.com')
        self.assertEqual(offset, len(wire))

    def test_decode_root(self):
        name, offset = codec.decode_name(b'\x00', 0)
        self.assertEqual(name, '')
        self.assertEqual(offset, 1)

    def test_decode_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.decode_name(u'\x00', 0)

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

    def test_decode_truncated_label(self):
        with self.assertRaises(ValueError):
            codec.decode_name(b'\x03ab', 0)

    def test_decode_invalid_label_type(self):
        with self.assertRaises(ValueError):
            codec.decode_name(b'\x80', 0)

    def test_decode_label_too_long(self):
        wire = b'\x40' + (b'a' * 64) + b'\x00'
        with self.assertRaises(ValueError):
            codec.decode_name(wire, 0)

    def test_decode_non_ascii_label(self):
        with self.assertRaises(UnicodeDecodeError):
            codec.decode_name(b'\x01\xff\x00', 0)

    def test_decode_truncated_pointer(self):
        with self.assertRaises(ValueError):
            codec.decode_name(b'\xc0', 0)

    def test_decode_pointer_out_of_range(self):
        with self.assertRaises(ValueError):
            codec.decode_name(b'\xc0\x10', 0)

    def test_decode_pointer_into_label(self):
        wire = b'\x03www\x07example\x03com\x00\xc0\x01'
        with self.assertRaises(ValueError):
            codec.decode_name(wire, 17)

    def test_decode_pointer_loop(self):
        with self.assertRaises(ValueError):
            codec.decode_name(b'\xc0\x00', 0)

    def test_skip_name(self):
        wire = b'\x07example\x03com\x00\xff\xff'
        offset = codec.skip_name(wire, 0)
        self.assertEqual(offset, 13)

    def test_skip_name_compression(self):
        wire = b'\x03sub\xc0\x00\xff\xff'
        offset = codec.skip_name(wire, 0)
        self.assertEqual(offset, 6)

    def test_skip_name_truncated_pointer(self):
        with self.assertRaises(ValueError):
            codec.skip_name(b'\xc0', 0)

    def test_skip_name_pointer_out_of_range(self):
        with self.assertRaises(ValueError):
            codec.skip_name(b'\xc0\x10', 0)

    def test_skip_name_invalid_label_type(self):
        with self.assertRaises(ValueError):
            codec.skip_name(b'\x80', 0)

    def test_skip_name_label_too_long(self):
        wire = b'\x40' + (b'a' * 64) + b'\x00'
        with self.assertRaises(ValueError):
            codec.skip_name(wire, 0)

    def test_skip_name_truncated_label(self):
        with self.assertRaises(ValueError):
            codec.skip_name(b'\x03ab', 0)

    def test_skip_name_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.skip_name(u'\x00', 0)


class TestQueryName(unittest.TestCase):
    """Tests for query name encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        base_domain = 'tunnel.example.com'
        encoded = codec.encode_query_name(data, base_domain, 1234)
        decoded = codec.decode_query_name(encoded, base_domain)
        self.assertEqual(decoded, data)

    def test_encode_decode_min_label_max_len(self):
        data = b'test'
        encoded = codec.encode_query_name(
            data, 'example.com', 0, label_max_len=codec.NONCE_LEN
        )
        decoded = codec.decode_query_name(
            encoded, 'example.com', label_max_len=codec.NONCE_LEN
        )
        self.assertEqual(decoded, data)

    def test_encode_decode_max_label_max_len(self):
        data = b'test'
        encoded = codec.encode_query_name(
            data, 'example.com', 0, label_max_len=codec.MAX_LABEL_LEN
        )
        decoded = codec.decode_query_name(
            encoded, 'example.com', label_max_len=codec.MAX_LABEL_LEN
        )
        self.assertEqual(decoded, data)

    def test_label_max_len_too_small(self):
        with self.assertRaises(ValueError):
            codec.encode_query_name(b'test', 'example.com', 0, label_max_len=3)

    def test_label_max_len_too_large(self):
        with self.assertRaises(ValueError):
            codec.encode_query_name(b'test', 'example.com', 0, label_max_len=70)

    def test_base_domain_required(self):
        with self.assertRaises(ValueError):
            codec.encode_query_name(b'test', '', 0)

    def test_encode_base_domain_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.encode_query_name(b'test', b'example.com', 0)

    def test_decode_base_domain_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.decode_query_name('abcd.example.com', b'example.com')

    def test_base_domain_trailing_dot(self):
        data = b'test'
        encoded = codec.encode_query_name(data, 'example.com.', 0)
        decoded = codec.decode_query_name(encoded, 'example.com')
        self.assertEqual(decoded, data)

    def test_base_domain_label_too_long(self):
        base_domain = '%s.com' % ('a' * 64)
        with self.assertRaises(ValueError):
            codec.encode_query_name(b'test', base_domain, 0)

    def test_base_domain_name_too_long(self):
        labels = ['a' * 63, 'b' * 63, 'c' * 63, 'd' * 58]
        base_domain = '.'.join(labels)
        with self.assertRaises(ValueError):
            codec.encode_query_name(b'', base_domain, 0)

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

    def test_nonce_wraparound(self):
        data = b'test'
        enc1 = codec.encode_query_name(data, 'example.com', -1)
        enc2 = codec.encode_query_name(data, 'example.com', 0xFFFF)
        self.assertEqual(enc1, enc2)

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

    def test_query_name_trailing_dot(self):
        data = b'test'
        encoded = codec.encode_query_name(data, 'example.com', 0)
        decoded = codec.decode_query_name(encoded + '.', 'example.com.')
        self.assertEqual(decoded, data)

    def test_decode_label_too_long(self):
        query_name = 'abcd.%s.example.com' % ('a' * 51)
        with self.assertRaises(ValueError):
            codec.decode_query_name(query_name, 'example.com')

    def test_decode_label_max_len_too_small(self):
        with self.assertRaises(ValueError):
            codec.decode_query_name(
                'abcd.example.com', 'example.com', label_max_len=3
            )

    def test_decode_label_max_len_too_large(self):
        with self.assertRaises(ValueError):
            codec.decode_query_name(
                'abcd.example.com', 'example.com', label_max_len=70
            )

    def test_decode_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.decode_query_name(b'abcd.example.com', 'example.com')

    def test_decode_base_domain_required(self):
        with self.assertRaises(ValueError):
            codec.decode_query_name('abcd.example.com', '')


class TestTxtRdata(unittest.TestCase):
    """Tests for TXT record RDATA encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        rdata = codec.encode_txt_rdata(data)
        decoded = codec.decode_txt_rdata(rdata)
        self.assertEqual(decoded, data)

    def test_encode_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.encode_txt_rdata(u'text')

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

    def test_decode_zero_length_string(self):
        rdata = b'\x00\x04QUJD'
        self.assertEqual(codec.decode_txt_rdata(rdata), b'ABC')

    def test_decode_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.decode_txt_rdata(u'text')

    def test_decode_invalid_base64_length(self):
        rdata = b'\x01A'
        with self.assertRaises(binascii.Error):
            codec.decode_txt_rdata(rdata)

    def test_decode_invalid_ascii_base64(self):
        rdata = b'\x04!!!!'
        self.assertEqual(codec.decode_txt_rdata(rdata), b'')

    def test_non_ascii_base64(self):
        rdata = b'\x01\xff'
        with self.assertRaises(UnicodeDecodeError):
            codec.decode_txt_rdata(rdata)


class TestCnameTarget(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        data = b'Hello World'
        suffix = 'c.example.com'
        encoded = codec.encode_cname_target(data, suffix)
        decoded = codec.decode_cname_target(encoded, suffix)
        self.assertEqual(decoded, data)

    def test_encode_decode_empty_suffix(self):
        data = b'test'
        encoded = codec.encode_cname_target(data, '')
        decoded = codec.decode_cname_target(encoded, '')
        self.assertEqual(decoded, data)

    def test_encode_rejects_text_data(self):
        with self.assertRaises(TypeError):
            codec.encode_cname_target(u'test', 'c.example.com')

    def test_encode_rejects_non_text_suffix(self):
        with self.assertRaises(TypeError):
            codec.encode_cname_target(b'test', b'c.example.com')

    def test_empty_data(self):
        encoded = codec.encode_cname_target(b'', 'c.example.com')
        with self.assertRaises(ValueError):
            codec.decode_cname_target(encoded, 'c.example.com')

    def test_decode_rejects_non_text(self):
        with self.assertRaises(TypeError):
            codec.decode_cname_target(b'abcd.c.example.com', 'c.example.com')

    def test_decode_rejects_non_text_suffix(self):
        with self.assertRaises(TypeError):
            codec.decode_cname_target('abcd.c.example.com', b'c.example.com')

    def test_case_insensitive(self):
        data = b'test'
        suffix = 'c.example.com'
        encoded = codec.encode_cname_target(data, suffix)
        decoded = codec.decode_cname_target(encoded.upper(), 'C.EXAMPLE.COM')
        self.assertEqual(decoded, data)

    def test_label_max_len(self):
        data = b'test'
        encoded = codec.encode_cname_target(
            data, 'c.example.com', label_max_len=codec.MAX_LABEL_LEN
        )
        decoded = codec.decode_cname_target(
            encoded, 'c.example.com', label_max_len=codec.MAX_LABEL_LEN
        )
        self.assertEqual(decoded, data)

    def test_label_max_len_too_small(self):
        with self.assertRaises(ValueError):
            codec.encode_cname_target(b'test', 'c.example.com',
                                      label_max_len=3)

    def test_label_max_len_too_large(self):
        with self.assertRaises(ValueError):
            codec.decode_cname_target('abcd.c.example.com', 'c.example.com',
                                      label_max_len=70)

    def test_decode_label_max_len_too_small(self):
        with self.assertRaises(ValueError):
            codec.decode_cname_target('abcd.c.example.com', 'c.example.com',
                                      label_max_len=3)

    def test_decode_label_too_long(self):
        target = '%s.c.example.com' % ('a' * 51)
        with self.assertRaises(ValueError):
            codec.decode_cname_target(target, 'c.example.com')

    def test_wrong_suffix(self):
        encoded = codec.encode_cname_target(b'test', 'c.example.com')
        with self.assertRaises(ValueError):
            codec.decode_cname_target(encoded, 'x.example.com')


class TestRdataUtilities(unittest.TestCase):
    def test_encode_a_rdata(self):
        addr = b'\x7f\x00\x00\x01'
        self.assertEqual(codec.encode_a_rdata(addr), addr)

    def test_encode_a_rdata_invalid_length(self):
        with self.assertRaises(ValueError):
            codec.encode_a_rdata(b'\x7f\x00\x00')

    def test_encode_a_rdata_rejects_text(self):
        with self.assertRaises(TypeError):
            codec.encode_a_rdata(u'test')

    def test_build_opt_record(self):
        record = codec.build_opt_record(udp_size=1234)
        expected = struct.pack('>BHHIH', 0, codec.QTYPE_OPT, 1234, 0, 0)
        self.assertEqual(record, expected)


class TestMtuCalculation(unittest.TestCase):
    """Tests for MTU calculation."""

    def test_query_mtu_short_domain(self):
        mtu = codec.calc_query_mtu('x.co')
        self.assertGreater(mtu, 140)

    def test_query_mtu_empty_base_domain(self):
        mtu_empty = codec.calc_query_mtu('')
        mtu_dot = codec.calc_query_mtu('.')
        self.assertEqual(mtu_empty, mtu_dot)
        self.assertGreater(mtu_empty, 0)

    def test_query_mtu_base_domain_label_too_long(self):
        base_domain = '%s.com' % ('a' * 64)
        with self.assertRaises(ValueError):
            codec.calc_query_mtu(base_domain)

    def test_query_mtu_base_domain_too_long(self):
        base_domain = '.'.join(['a' * 63] * 4)
        self.assertEqual(codec.calc_query_mtu(base_domain), 0)

    def test_query_mtu_custom_label_max_len(self):
        mtu_default = codec.calc_query_mtu('example.com')
        mtu_small = codec.calc_query_mtu('example.com', label_max_len=20)
        self.assertLess(mtu_small, mtu_default)

    def test_query_mtu_invalid_label_max_len_small(self):
        with self.assertRaises(ValueError):
            codec.calc_query_mtu('example.com', label_max_len=3)

    def test_query_mtu_invalid_label_max_len_large(self):
        with self.assertRaises(ValueError):
            codec.calc_query_mtu('example.com', label_max_len=70)

    def test_query_mtu_long_domain(self):
        mtu = codec.calc_query_mtu('very.long.subdomain.example.com')
        self.assertGreater(mtu, 100)
        self.assertLess(mtu, 150)

    def test_query_mtu_domain_too_long(self):
        label = 'a' * 61
        base_domain = '.'.join([label] * 4)  # length = 247
        mtu = codec.calc_query_mtu(base_domain)
        self.assertEqual(mtu, 0)

    def test_query_mtu_decreases_with_domain_length(self):
        mtu_short = codec.calc_query_mtu('x.co')
        mtu_long = codec.calc_query_mtu('tunnel.example.com')
        self.assertGreater(mtu_short, mtu_long)

    def test_response_mtu_standard(self):
        mtu = codec.calc_response_mtu(codec.QTYPE_TXT, 512)
        self.assertEqual(mtu, 191)  # floor(255 * 3 / 4)

    def test_response_mtu_standard_when_edns_small(self):
        mtu_std = codec.calc_response_mtu(codec.QTYPE_TXT, 512)
        mtu_small = codec.calc_response_mtu(codec.QTYPE_TXT, 0)
        self.assertEqual(mtu_small, mtu_std)

    def test_response_mtu_a_records(self):
        self.assertEqual(codec.calc_response_mtu(codec.QTYPE_A, 512), 0)
        self.assertEqual(codec.calc_response_mtu(codec.QTYPE_AAAA, 512), 0)

    def test_response_mtu_null(self):
        mtu = codec.calc_response_mtu(codec.QTYPE_NULL, 512)
        self.assertEqual(mtu, 350)

    def test_response_mtu_null_too_small(self):
        mtu = codec.calc_response_mtu(codec.QTYPE_NULL, 40)
        self.assertEqual(mtu, 0)

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

    def test_response_mtu_cname_suffix_too_long(self):
        suffix = 'a' * codec.MAX_NAME_LEN
        self.assertEqual(
            codec.calc_response_mtu(codec.QTYPE_CNAME, 512, suffix),
            0
        )

    def test_response_mtu_cname_label_max_len(self):
        mtu_default = codec.calc_response_mtu(
            codec.QTYPE_CNAME, 512, 'c.example.com'
        )
        mtu_small = codec.calc_response_mtu(
            codec.QTYPE_CNAME, 512, 'c.example.com', label_max_len=20
        )
        self.assertLess(mtu_small, mtu_default)

    def test_response_mtu_cname_label_max_len_too_small(self):
        with self.assertRaises(ValueError):
            codec.calc_response_mtu(
                codec.QTYPE_CNAME, 512, 'c.example.com', label_max_len=3
            )

    def test_response_mtu_cname_label_max_len_too_large(self):
        with self.assertRaises(ValueError):
            codec.calc_response_mtu(
                codec.QTYPE_CNAME, 512, 'c.example.com', label_max_len=70
            )

    def test_response_mtu_cname_requires_suffix(self):
        with self.assertRaises(ValueError):
            codec.calc_response_mtu(codec.QTYPE_CNAME, 512)

    def test_response_mtu_unsupported_type(self):
        with self.assertRaises(ValueError):
            codec.calc_response_mtu(999, 512)

    def test_cname_payload_cap_bounds(self):
        cap = codec.calc_cname_payload_cap('example.com', 'c.example.com')
        self.assertGreater(cap, 0)
        self.assertLessEqual(cap, codec.calc_query_mtu('example.com'))
        self.assertEqual(
            codec.calc_cname_payload_cap('example.com', 'c.example.com',
                                         max_packet_size=0),
            0
        )
        self.assertEqual(
            codec.calc_cname_payload_cap('example.com', 'c.example.com',
                                         max_packet_size=None),
            0
        )

    def test_cname_payload_cap_invalid_label_max_len(self):
        with self.assertRaises(ValueError):
            codec.calc_cname_payload_cap(
                'example.com', 'c.example.com', label_max_len=3
            )

    def test_cname_payload_cap_base_domain_label_too_long(self):
        base_domain = '%s.com' % ('a' * 64)
        with self.assertRaises(ValueError):
            codec.calc_cname_payload_cap(base_domain, 'c.example.com')

    def test_cname_payload_cap_base_domain_too_long(self):
        base_domain = '.'.join(['a' * 63] * 4)
        self.assertEqual(
            codec.calc_cname_payload_cap(base_domain, 'c.example.com'),
            0
        )

    def test_cname_payload_cap_suffix_label_too_long(self):
        suffix = '%s.com' % ('a' * 64)
        with self.assertRaises(ValueError):
            codec.calc_cname_payload_cap('example.com', suffix)


if __name__ == '__main__':
    unittest.main()
