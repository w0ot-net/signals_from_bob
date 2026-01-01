# -*- coding: ascii -*-
from __future__ import absolute_import

import socket
import struct
import unittest

from sfb.transport.icmp.icmp_packet import (
    ICMP_ECHO_REPLY,
    ICMP_ECHO_REQUEST,
    build_echo_reply,
    build_echo_request,
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
        result = parse_icmp_echo(packet, expect_type=ICMP_ECHO_REQUEST)
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
        packet = ip_header + icmp
        result = parse_icmp_echo(packet, expect_type=ICMP_ECHO_REPLY)
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
        result = parse_icmp_echo(bytes(bad), expect_type=ICMP_ECHO_REQUEST)
        self.assertIsNotNone(result)
        result = parse_icmp_echo(
            bytes(bad),
            expect_type=ICMP_ECHO_REQUEST,
            validate_checksum=True,
        )
        self.assertIsNone(result)

    def test_parse_rejects_wrong_type(self):
        packet = build_echo_reply(0x1, 0x2, b'data')
        result = parse_icmp_echo(packet, expect_type=ICMP_ECHO_REQUEST)
        self.assertIsNone(result)
