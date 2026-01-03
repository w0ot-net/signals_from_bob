# -*- coding: ascii -*-
from __future__ import absolute_import

import struct
import unittest

from sfb.transport.dns import codec
from sfb.transport.dns.dns_client import DnsClient, _PendingQuery
from sfb.transport.transport_base import PendingTracker


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)


class DnsClientTests(unittest.TestCase):
    def _make_client(self, qtype=codec.QTYPE_A, rtype=codec.QTYPE_CNAME,
                     edns_size=512):
        client = DnsClient.__new__(DnsClient)
        client._qtype = qtype
        client._rtype = rtype
        client._edns_size = edns_size
        client._cname_suffix = 'c.example.com'
        client._label_max_len = 50
        return client

    def _make_send_client(self):
        client = DnsClient.__new__(DnsClient)
        client._max_in_flight = 5
        client._send_mtu = 1024
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        client._next_corr_id = 0
        client._resolver = ('127.0.0.1', 53)
        client._sock = DummySock()
        client._encode_query = lambda data: 'q.example.com'
        client._build_query = lambda dns_id, name: b'packet'
        client._next_query_id = lambda: 1
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
        if rtype == codec.QTYPE_CNAME:
            target = codec.encode_cname_target(payload, 'c.example.com')
            rdata = codec.encode_name(target)
        else:
            rdata = b''
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
        if rtype == codec.QTYPE_CNAME:
            target = codec.encode_cname_target(payload, 'c.example.com')
            rdata = codec.encode_name(target)
        else:
            rdata = b''
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
        self.assertEqual(qtype, codec.QTYPE_A)
        self.assertEqual(qclass, codec.QCLASS_IN)

    def test_parse_response_cname(self):
        client = self._make_client()
        payload = b'hello'
        data = self._build_response(0x1, 'example.com', codec.QTYPE_CNAME,
                                    payload)
        query_id, response_payload = client._parse_response(data)
        self.assertEqual(query_id, 0x1)
        self.assertEqual(response_payload, payload)

    def test_parse_response_rcode_error(self):
        client = self._make_client()
        payload = b'ignored'
        data = self._build_response(0x2, 'example.com', codec.QTYPE_CNAME,
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
            0x3, 'example.com', codec.QTYPE_CNAME, b'hello', 2
        )
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x3)
        self.assertIsNone(payload)

    def test_parse_response_rejects_truncated_rdata(self):
        client = self._make_client()
        data = self._build_response_truncated_rdata(
            0x4, 'example.com', codec.QTYPE_CNAME
        )
        query_id, payload = client._parse_response(data)
        self.assertEqual(query_id, 0x4)
        self.assertIsNone(payload)

    def test_parse_response_rejects_wrong_type(self):
        client = self._make_client()
        payload = b'hello'
        data = self._build_response(0x5, 'example.com', codec.QTYPE_A,
                                    payload)
        query_id, response_payload = client._parse_response(data)
        self.assertEqual(query_id, 0x5)
        self.assertIsNone(response_payload)

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
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
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

    def test_send_prunes_once(self):
        client = self._make_send_client()
        orig_prune = PendingTracker.prune
        calls = []

        def counting_prune(self, now=None):
            calls.append(now)
            return orig_prune(self, now=now)

        PendingTracker.prune = counting_prune
        try:
            permit = client.reserve_send(now=1)
            self.assertIsNotNone(permit)
            client.send(b'test', permit)
        finally:
            PendingTracker.prune = orig_prune

        self.assertEqual(len(calls), 1)

    def test_reserve_send_prunes_stale(self):
        client = DnsClient.__new__(DnsClient)
        client._max_in_flight = 5
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        pending = _PendingQuery(100, 'q.example.com')
        client._pending.add(1, pending, now=0)
        client._dns_to_corr[100] = 1
        permit = client.reserve_send(now=2)
        self.assertIsNotNone(permit)
        count = client.pending_count()
        self.assertEqual(count, 0)
        self.assertEqual(client._dns_to_corr, {})


if __name__ == '__main__':
    unittest.main()
