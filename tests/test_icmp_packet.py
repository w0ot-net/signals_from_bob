# -*- coding: ascii -*-
from __future__ import absolute_import

import socket
import struct
import unittest

from sfb.compat import to_bytes
from sfb.transport.icmp.icmp_packet import (
    ICMP_ECHO_REPLY,
    ICMP_ECHO_REQUEST,
    build_echo_reply,
    build_echo_request,
    checksum,
    parse_icmp_echo,
)


class IcmpPacketTests(unittest.TestCase):
    def _build_ipv4_header(self, payload_len, proto):
        ver_ihl = 0x45
        tos = 0
        total_len = 20 + payload_len
        identification = 0
        flags_fragment = 0
        ttl = 64
        checksum = 0
        src = socket.inet_aton('1.2.3.4')
        dst = socket.inet_aton('5.6.7.8')
        return struct.pack(
            '>BBHHHBBH4s4s',
            ver_ihl,
            tos,
            total_len,
            identification,
            flags_fragment,
            ttl,
            proto,
            checksum,
            src,
            dst,
        )

    def test_build_and_parse_request(self):
        ident = 0x1234
        seq = 0x0001
        payload = b'hello'
        packet = build_echo_request(ident, seq, payload)
        result, reason = parse_icmp_echo(
            packet,
            expect_type=ICMP_ECHO_REQUEST,
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        icmp_type, parsed_ident, parsed_seq, parsed_payload = result
        self.assertEqual(icmp_type, ICMP_ECHO_REQUEST)
        self.assertEqual(parsed_ident, ident)
        self.assertEqual(parsed_seq, seq)
        self.assertEqual(parsed_payload, payload)

    def test_build_and_parse_reply_with_ip_header(self):
        ident = 0xBEEF
        seq = 0x0102
        payload = b'abc'
        icmp = build_echo_reply(ident, seq, payload)
        ip_header = self._build_ipv4_header(len(icmp), socket.IPPROTO_ICMP)
        packet = ip_header + to_bytes(icmp)
        result, reason = parse_icmp_echo(
            packet,
            expect_type=ICMP_ECHO_REPLY,
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        icmp_type, parsed_ident, parsed_seq, parsed_payload = result
        self.assertEqual(icmp_type, ICMP_ECHO_REPLY)
        self.assertEqual(parsed_ident, ident)
        self.assertEqual(parsed_seq, seq)
        self.assertEqual(parsed_payload, payload)

    def test_parse_bad_checksum_optional(self):
        packet = build_echo_request(0x1, 0x2, b'data')
        bad = bytearray(packet)
        bad[-1] ^= 0xFF
        result, reason = parse_icmp_echo(
            bytes(bad),
            expect_type=ICMP_ECHO_REQUEST,
        )
        self.assertIsNone(reason)
        self.assertIsNotNone(result)
        result, reason = parse_icmp_echo(
            bytes(bad),
            expect_type=ICMP_ECHO_REQUEST,
            validate_checksum=True,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, 'bad_checksum')

    def test_parse_rejects_wrong_type(self):
        packet = build_echo_reply(0x1, 0x2, b'data')
        result, reason = parse_icmp_echo(
            packet,
            expect_type=ICMP_ECHO_REQUEST,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, 'type_mismatch')

    def test_parse_rejects_wrong_ident(self):
        packet = build_echo_request(0x1111, 0x2, b'data')
        result, reason = parse_icmp_echo(
            packet,
            expect_ident=0x2222,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, 'ident_mismatch')

    def test_parse_short_packet_reason(self):
        result, reason = parse_icmp_echo(b'\x08\x00')
        self.assertIsNone(result)
        self.assertEqual(reason, 'short_packet')

    def test_parse_not_icmp_reason(self):
        payload_len = 8
        ip_header = self._build_ipv4_header(payload_len, socket.IPPROTO_TCP)
        packet = ip_header + (b'\x00' * payload_len)
        result, reason = parse_icmp_echo(packet)
        self.assertIsNone(result)
        self.assertEqual(reason, 'not_icmp')

    def test_checksum_known_vector_even(self):
        header = struct.pack('>BBHHH', ICMP_ECHO_REQUEST, 0, 0, 0x1234, 0x0001)
        packet = header + b'ping'
        self.assertEqual(checksum(packet), 0x06FA)

    def test_checksum_known_vector_odd(self):
        header = struct.pack('>BBHHH', ICMP_ECHO_REPLY, 0, 0, 0xBEEF, 0x0102)
        packet = header + b'hello'
        self.assertEqual(checksum(packet), 0xFC3B)

    def test_checksum_zero_for_built_packets(self):
        request = build_echo_request(0x1234, 0x0001, b'ping')
        reply = build_echo_reply(0xBEEF, 0x0102, b'hello')
        self.assertEqual(checksum(request), 0)
        self.assertEqual(checksum(reply), 0)
