# -*- coding: ascii -*-
from __future__ import absolute_import

import logging
import struct
import unittest

from sfb.config import DNS_STANDARD_SIZE
from sfb.transport.dns import codec
from sfb.transport.dns.dns_server import DnsServer
from sfb.transport.transport_base import TransportError


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)


class DnsServerTests(unittest.TestCase):
    def _make_server(self, rtype=codec.QTYPE_CNAME, edns_size=DNS_STANDARD_SIZE):
        server = DnsServer.__new__(DnsServer)
        server._rtype = rtype
        server._edns_size = edns_size
        server._label_max_len = 50
        server._cname_suffix = 'c.example.com'
        server._opt_record = b''
        server._opt_arcount = 0
        server._opt_record_len = len(server._opt_record)
        server._sock = DummySock()
        server._logger = logging.getLogger('test')
        server._base_domain = 'example.com'
        server._cname_a_addr_bytes = b'\x7f\x00\x00\x01'
        server._soa_record = server._build_soa_record()
        return server

    def _build_query(self, query_id, qname, qtype, flags=0,
                     qdcount=1, qclass=codec.QCLASS_IN):
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            qdcount,
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, qclass)
        return header + question

    def _make_long_qname(self):
        labels = [
            'a' * 63,
            'b' * 63,
            'c' * 63,
            'd' * 61,
        ]
        qname = '.'.join(labels)
        self.assertEqual(len(qname), 253)
        return qname

    def test_parse_query_roundtrip(self):
        server = self._make_server()
        data = self._build_query(0x33, 'tunnel.example.com',
                                 codec.QTYPE_A)
        query_id, qname, qtype = server._parse_query(data)
        self.assertEqual(query_id, 0x33)
        self.assertEqual(qname, 'tunnel.example.com')
        self.assertEqual(qtype, codec.QTYPE_A)

    def test_parse_query_rejects_compression_loop(self):
        server = self._make_server()
        header = struct.pack('>HHHHHH', 1, 0, 1, 0, 0, 0)
        # QNAME starts with a compression pointer to itself (loop)
        question = b'\xc0\x0c' + struct.pack('>HH', codec.QTYPE_A,
                                             codec.QCLASS_IN)
        data = header + question
        with self.assertRaises(ValueError):
            server._parse_query(data)

    def test_parse_query_rejects_non_query(self):
        server = self._make_server()
        data = self._build_query(0x1, 'tunnel.example.com',
                                 codec.QTYPE_A, flags=codec.FLAG_QR)
        with self.assertRaises(ValueError):
            server._parse_query(data)

    def test_parse_query_rejects_no_question(self):
        server = self._make_server()
        data = self._build_query(0x2, 'tunnel.example.com',
                                 codec.QTYPE_A, qdcount=0)
        with self.assertRaises(ValueError):
            server._parse_query(data)

    def test_parse_query_rejects_wrong_class(self):
        server = self._make_server()
        data = self._build_query(0x3, 'tunnel.example.com',
                                 codec.QTYPE_A, qclass=2)
        with self.assertRaises(ValueError):
            server._parse_query(data)

    def test_parse_query_rejects_truncated_question(self):
        server = self._make_server()
        header = struct.pack('>HHHHHH', 1, 0, 1, 0, 0, 0)
        data = header + b'\x00'
        with self.assertRaises(ValueError):
            server._parse_query(data)

    def test_send_response_builds_cname(self):
        server = self._make_server()
        payload = b'hello'
        server._send_response(
            0x10,
            'tunnel.example.com',
            codec.QTYPE_A,
            payload,
            ('127.0.0.1', 5353),
            payload_cap=None,
            qname_wire_len=None,
            max_packet_size=None,
        )
        response, _ = server._sock.sent[0]
        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', response[:12]
        )
        self.assertEqual(query_id, 0x10)
        self.assertTrue(flags & codec.FLAG_QR)
        self.assertTrue(flags & codec.FLAG_AA)
        self.assertEqual(qdcount, 1)
        self.assertEqual(ancount, 1)
        self.assertEqual(nscount, 0)
        self.assertEqual(arcount, 0)

        offset = 12
        qname, offset = codec.decode_name(response, offset)
        self.assertEqual(qname, 'tunnel.example.com')
        qtype, qclass = struct.unpack('>HH', response[offset:offset + 4])
        self.assertEqual(qtype, codec.QTYPE_A)
        self.assertEqual(qclass, codec.QCLASS_IN)
        offset += 4

        answer_name, offset = codec.decode_name(response, offset)
        self.assertEqual(answer_name, qname)
        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', response[offset:offset + 10]
        )
        self.assertEqual(rtype, codec.QTYPE_CNAME)
        self.assertEqual(rclass, codec.QCLASS_IN)
        self.assertEqual(ttl, 0)
        offset += 10
        rdata = response[offset:offset + rdlength]
        target_name, _ = codec.decode_name(rdata, 0)
        decoded = codec.decode_cname_target(
            target_name, server._cname_suffix, server._label_max_len
        )
        self.assertEqual(decoded, payload)

    def test_send_response_rejects_non_cname_rtype(self):
        server = self._make_server(rtype=codec.QTYPE_A)
        with self.assertRaises(TransportError):
            server._send_response(
                0x11,
                'tunnel.example.com',
                codec.QTYPE_A,
                b'hi',
                ('127.0.0.1', 5353),
                payload_cap=None,
                qname_wire_len=None,
                max_packet_size=None,
            )

    def test_send_response_rejects_invalid_payload(self):
        server = self._make_server()
        with self.assertRaises(TransportError):
            server._send_response(
                0x12,
                'tunnel.example.com',
                codec.QTYPE_A,
                b'a' * 1024,
                ('127.0.0.1', 5353),
                payload_cap=None,
                qname_wire_len=None,
                max_packet_size=None,
            )

    def test_send_empty_response_includes_soa(self):
        server = self._make_server()
        server._send_empty_response(
            0x20,
            'tunnel.example.com',
            codec.QTYPE_A,
            ('127.0.0.1', 5353),
            reason='test',
        )
        response, _ = server._sock.sent[0]
        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', response[:12]
        )
        self.assertEqual(query_id, 0x20)
        self.assertTrue(flags & codec.FLAG_QR)
        self.assertTrue(flags & codec.FLAG_AA)
        self.assertEqual(qdcount, 1)
        self.assertEqual(ancount, 0)
        self.assertEqual(nscount, 1)
        self.assertEqual(arcount, 0)

        offset = 12
        qname, offset = codec.decode_name(response, offset)
        self.assertEqual(qname, 'tunnel.example.com')
        offset += 4  # QTYPE/QCLASS

        soa_name, offset = codec.decode_name(response, offset)
        self.assertEqual(soa_name, server._base_domain)
        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', response[offset:offset + 10]
        )
        self.assertEqual(rtype, codec.QTYPE_SOA)
        self.assertEqual(rclass, codec.QCLASS_IN)
        self.assertEqual(ttl, 0)
        self.assertEqual(len(response) - (offset + 10), rdlength)

    def test_send_cname_followup_returns_a_record(self):
        server = self._make_server()
        server._send_cname_followup(
            0x21,
            'c.example.com',
            codec.QTYPE_A,
            ('127.0.0.1', 5353),
        )
        response, _ = server._sock.sent[0]
        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', response[:12]
        )
        self.assertEqual(query_id, 0x21)
        self.assertTrue(flags & codec.FLAG_QR)
        self.assertTrue(flags & codec.FLAG_AA)
        self.assertEqual(qdcount, 1)
        self.assertEqual(ancount, 1)
        self.assertEqual(nscount, 0)
        self.assertEqual(arcount, 0)

        offset = 12
        qname, offset = codec.decode_name(response, offset)
        self.assertEqual(qname, 'c.example.com')
        qtype, qclass = struct.unpack('>HH', response[offset:offset + 4])
        self.assertEqual(qtype, codec.QTYPE_A)
        self.assertEqual(qclass, codec.QCLASS_IN)
        offset += 4

        answer_name, offset = codec.decode_name(response, offset)
        self.assertEqual(answer_name, qname)
        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', response[offset:offset + 10]
        )
        self.assertEqual(rtype, codec.QTYPE_A)
        self.assertEqual(rclass, codec.QCLASS_IN)
        self.assertEqual(ttl, 0)
        self.assertEqual(rdlength, 4)
        offset += 10
        rdata = response[offset:offset + rdlength]
        self.assertEqual(rdata, server._cname_a_addr_bytes)

    def test_response_payload_cap_non_cname(self):
        server = self._make_server(rtype=codec.QTYPE_A)
        payload_cap, qname_wire_len, max_packet_size = (
            server._response_payload_cap('tunnel.example.com')
        )
        self.assertIsNone(payload_cap)
        self.assertIsNone(qname_wire_len)
        self.assertIsNone(max_packet_size)

    def test_response_payload_cap_fits_max_packet(self):
        server = self._make_server()
        server._edns_size = 1232
        server._opt_record_len = len(codec.build_opt_record(server._edns_size))
        qname = 'tunnel.example.com'
        payload_cap, qname_wire_len, max_packet_size = (
            server._response_payload_cap(qname)
        )
        self.assertGreater(payload_cap, 0)
        self.assertEqual(qname_wire_len, len(codec.encode_name(qname)))
        self.assertEqual(max_packet_size, server._edns_size)

        question_len = qname_wire_len + 4
        answer_name_len = qname_wire_len
        answer_fixed_len = 10
        additional_len = server._opt_record_len
        fixed_len = (12 + question_len + answer_name_len +
                     answer_fixed_len + additional_len)
        cname_target = codec.encode_cname_target(
            b'\x00' * payload_cap, server._cname_suffix, server._label_max_len
        )
        rdata_len = len(codec.encode_name(cname_target))
        total_len = fixed_len + rdata_len
        self.assertLessEqual(total_len, max_packet_size)

    def test_response_payload_cap_zero_when_fixed_len_too_large(self):
        server = self._make_server()
        server._edns_size = DNS_STANDARD_SIZE
        server._opt_record_len = 0
        qname = self._make_long_qname()
        payload_cap, qname_wire_len, max_packet_size = (
            server._response_payload_cap(qname)
        )
        self.assertEqual(payload_cap, 0)
        self.assertEqual(qname_wire_len, len(codec.encode_name(qname)))
        self.assertEqual(max_packet_size, DNS_STANDARD_SIZE)


if __name__ == '__main__':
    unittest.main()
