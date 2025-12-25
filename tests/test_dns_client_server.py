# -*- coding: ascii -*-
from __future__ import absolute_import

import struct
import unittest

from tunnel.transport.dns import codec
from tunnel.transport.dns.dns_client import DnsClient
from tunnel.transport.dns.dns_server import DnsServer


class DnsClientTests(unittest.TestCase):
    def _make_client(self, qtype=codec.QTYPE_TXT, edns_size=512):
        client = DnsClient.__new__(DnsClient)
        client._qtype = qtype
        client._edns_size = edns_size
        return client

    def _build_response(self, query_id, qname, rtype, payload,
                        flags_extra=0):
        flags = codec.FLAG_QR | codec.FLAG_AA | flags_extra
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', rtype, codec.QCLASS_IN)

        answer = codec.encode_name(qname)
        if rtype == codec.QTYPE_TXT:
            rdata = codec.encode_txt_rdata(payload)
        else:
            rdata = codec.base64_encode(payload).encode('ascii')
        answer += struct.pack('>HHIH',
            rtype,
            codec.QCLASS_IN,
            0,  # TTL
            len(rdata),
        )
        answer += rdata

        return header + question + answer

    def _build_response_with_rclass(self, query_id, qname, rtype, payload,
                                    rclass):
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', rtype, codec.QCLASS_IN)
        answer = codec.encode_name(qname)
        if rtype == codec.QTYPE_TXT:
            rdata = codec.encode_txt_rdata(payload)
        else:
            rdata = codec.base64_encode(payload).encode('ascii')
        answer += struct.pack('>HHIH',
            rtype,
            rclass,
            0,
            len(rdata),
        )
        answer += rdata
        return header + question + answer

    def _build_response_truncated_rdata(self, query_id, qname, rtype):
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', rtype, codec.QCLASS_IN)
        answer = codec.encode_name(qname)
        answer += struct.pack('>HHIH',
            rtype,
            codec.QCLASS_IN,
            0,
            10,  # rdlength larger than payload
        )
        answer += b'abc'
        return header + question + answer

    def test_build_query_encodes_name(self):
        client = self._make_client()
        query_id = 0x1234
        name = 'a.example.com'
        query = client._build_query(query_id, name)

        qid, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', query[:12]
        )
        self.assertEqual(qid, query_id)
        self.assertEqual(flags, codec.FLAG_RD)
        self.assertEqual(qdcount, 1)
        self.assertEqual(ancount, 0)
        self.assertEqual(nscount, 0)
        self.assertEqual(arcount, 0)

        decoded_name, offset = codec.decode_name(query, 12)
        self.assertEqual(decoded_name, name)
        qtype, qclass = struct.unpack('>HH', query[offset:offset + 4])
        self.assertEqual(qtype, codec.QTYPE_TXT)
        self.assertEqual(qclass, codec.QCLASS_IN)

    def test_parse_response_txt(self):
        client = self._make_client()
        payload = b'hello'
        data = self._build_response(0x1, 'example.com', codec.QTYPE_TXT,
                                    payload)
        query_id, response_payload = client._parse_response(data)
        self.assertEqual(query_id, 0x1)
        self.assertEqual(response_payload, payload)

    def test_parse_response_rcode_error(self):
        client = self._make_client()
        payload = b'ignored'
        data = self._build_response(0x2, 'example.com', codec.QTYPE_TXT,
                                    payload, flags_extra=codec.RCODE_NXDOMAIN)
        query_id, response_payload = client._parse_response(data)
        self.assertEqual(query_id, 0x2)
        self.assertIsNone(response_payload)

    def test_parse_response_rejects_non_response(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH', 1, 0, 0, 0, 0, 0)
        query_id, payload = client._parse_response(header)
        self.assertEqual(query_id, 1)
        self.assertIsNone(payload)

    def test_parse_response_too_short(self):
        client = self._make_client()
        self.assertIsNone(client._parse_response(b'\x00\x01'))

    def test_parse_response_rejects_wrong_class(self):
        client = self._make_client()
        data = self._build_response_with_rclass(
            0x3, 'example.com', codec.QTYPE_TXT, b'hello', 2
        )
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x3)
        self.assertIsNone(payload)

    def test_parse_response_rejects_truncated_rdata(self):
        client = self._make_client()
        data = self._build_response_truncated_rdata(
            0x4, 'example.com', codec.QTYPE_TXT
        )
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x4)
        self.assertIsNone(payload)

    def test_parse_response_rejects_invalid_null(self):
        client = self._make_client(qtype=codec.QTYPE_NULL)
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH', 0x5, flags, 1, 1, 0, 0)
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_NULL, codec.QCLASS_IN)
        answer = codec.encode_name('example.com')
        rdata = b'\xff\xff'
        answer += struct.pack('>HHIH',
            codec.QTYPE_NULL,
            codec.QCLASS_IN,
            0,
            len(rdata),
        )
        data = header + question + answer + rdata
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x5)
        self.assertIsNone(payload)

    def test_parse_response_no_answer(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x6,
            codec.FLAG_QR | codec.FLAG_AA,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_TXT, codec.QCLASS_IN)
        query_id, payload = client._parse_response(header + question)
        self.assertEqual(query_id, 0x6)
        self.assertIsNone(payload)

    def test_parse_response_truncated_question(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x7,
            codec.FLAG_QR | codec.FLAG_AA,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        # QNAME root but no QTYPE/QCLASS
        data = header + b'\x00'
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x7)
        self.assertIsNone(payload)

    def test_split_nameserver_values(self):
        client = self._make_client()
        values = [
            '8.8.8.8,8.8.4.4',
            '1.1.1.1 9.9.9.9',
            '8.8.8.8',
            '',
        ]
        hosts = client._split_nameserver_values(values)
        self.assertEqual(
            hosts,
            ['8.8.8.8', '8.8.4.4', '1.1.1.1', '9.9.9.9']
        )


class DnsServerTests(unittest.TestCase):
    def _make_server(self):
        return DnsServer.__new__(DnsServer)

    def _build_query(self, query_id, qname, qtype):
        header = struct.pack('>HHHHHH',
            query_id,
            0,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)
        return header + question

    def test_parse_query_roundtrip(self):
        server = self._make_server()
        data = self._build_query(0x33, 'tunnel.example.com',
                                 codec.QTYPE_TXT)
        query_id, qname, qtype = server._parse_query(data)
        self.assertEqual(query_id, 0x33)
        self.assertEqual(qname, 'tunnel.example.com')
        self.assertEqual(qtype, codec.QTYPE_TXT)

    def test_parse_query_rejects_compression(self):
        server = self._make_server()
        header = struct.pack('>HHHHHH', 1, 0, 1, 0, 0, 0)
        # QNAME starts with compression pointer (invalid in query)
        question = b'\xc0\x0c' + struct.pack('>HH', codec.QTYPE_TXT,
                                             codec.QCLASS_IN)
        data = header + question
        with self.assertRaises(ValueError):
            server._parse_query(data)


if __name__ == '__main__':
    unittest.main()
