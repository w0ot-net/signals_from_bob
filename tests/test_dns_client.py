# -*- coding: ascii -*-
"""Tests for DNS client transport."""

from __future__ import absolute_import

import struct
import unittest

from sfb.transport.dns import codec
from sfb.transport.dns.dns_client import DnsClient, _PendingQuery
from sfb.transport.transport_base import PendingTracker, TransportError


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)


class DummyRecvSock(object):
    def __init__(self, data, addr=None):
        self._data = data
        self._addr = addr or ('127.0.0.1', 53)

    def recvfrom(self, bufsize):
        return self._data, self._addr


class DummyCloseSock(object):
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class DnsClientTests(unittest.TestCase):
    def _make_client(self, qtype=codec.QTYPE_A, rtype=codec.QTYPE_CNAME,
                     edns_size=512):
        client = DnsClient.__new__(DnsClient)
        client._qtype = qtype
        client._rtype = rtype
        client._edns_size = edns_size
        client._cname_suffix = 'c.example.com'
        client._label_max_len = 50
        client._opt_record = b''
        client._opt_arcount = 0
        return client

    def _make_send_client(self, send_mtu=1024):
        client = DnsClient.__new__(DnsClient)
        client._max_in_flight = 5
        client._send_mtu = send_mtu
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        client._next_corr_id = 0
        client._resolver = ('127.0.0.1', 53)
        client._sock = DummySock()
        client._encode_query = lambda data: 'Q.EXAMPLE.COM'
        client._build_query = lambda dns_id, name: b'packet'
        client._next_query_id = lambda: 1
        return client

    def _make_recv_client(self, resp_data, dns_id, qname):
        client = self._make_client()
        client._sock = DummyRecvSock(resp_data)
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {dns_id: 7}
        client._pending.add(7, _PendingQuery(dns_id, qname.lower()), now=0)
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

    def test_encode_query_increments_nonce(self):
        client = DnsClient.__new__(DnsClient)
        client._nonce = 0x1234
        client._base_domain = 'example.com'
        client._label_max_len = 50
        data = b'hi'
        name = client._encode_query(data)
        expected = codec.encode_query_name(data, 'example.com', 0x1234, 50)
        self.assertEqual(name, expected)
        self.assertEqual(client._nonce, 0x1235)
        name = client._encode_query(data)
        expected = codec.encode_query_name(data, 'example.com', 0x1235, 50)
        self.assertEqual(name, expected)
        self.assertEqual(client._nonce, 0x1236)

    def test_next_query_id_wraps(self):
        client = DnsClient.__new__(DnsClient)
        client._query_id = 0xFFFF
        self.assertEqual(client._next_query_id(), 0xFFFF)
        self.assertEqual(client._query_id, 0)
        self.assertEqual(client._next_query_id(), 0)

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

    def test_build_query_appends_opt_record(self):
        client = self._make_client()
        client._opt_record = codec.build_opt_record(1234)
        client._opt_arcount = 1
        query = client._build_query(0x5, 'a.example.com')
        header = struct.unpack('>HHHHHH', query[:12])
        self.assertEqual(header[5], 1)
        self.assertTrue(query.endswith(client._opt_record))

    def test_parse_response_cname(self):
        client = self._make_client()
        payload = b'hello'
        data = self._build_response(0x1, 'Example.COM', codec.QTYPE_CNAME,
                                    payload)
        query_id, qname, response_payload, rcode, reason = client._parse_response(data)
        self.assertEqual(query_id, 0x1)
        self.assertEqual(qname, 'example.com')
        self.assertEqual(response_payload, payload)
        self.assertEqual(rcode, codec.RCODE_NOERROR)
        self.assertEqual(reason, 'ok')

    def test_parse_response_rcode_error(self):
        client = self._make_client()
        payload = b'ignored'
        data = self._build_response(0x2, 'example.com', codec.QTYPE_CNAME,
                                    payload, flags_extra=codec.RCODE_NXDOMAIN)
        result = client._parse_response(data)
        self.assertEqual(
            result,
            (0x2, None, None, codec.RCODE_NXDOMAIN, 'rcode'),
        )

    def test_parse_response_rejects_non_response(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH', 1, 0, 0, 0, 0, 0)
        result = client._parse_response(header)
        self.assertEqual(result, (1, None, None, None, 'not_response'))

    def test_parse_response_too_short(self):
        client = self._make_client()
        self.assertIsNone(client._parse_response(b'\x00\x01'))

    def test_parse_response_rejects_wrong_class(self):
        client = self._make_client()
        data = self._build_response_with_rclass(
            0x3, 'example.com', codec.QTYPE_CNAME, b'hello', 2
        )
        result = client._parse_response(data)
        self.assertEqual(
            result,
            (0x3, 'example.com', None, codec.RCODE_NOERROR, 'no_matching_answer'),
        )

    def test_parse_response_rejects_truncated_rdata(self):
        client = self._make_client()
        data = self._build_response_truncated_rdata(
            0x4, 'example.com', codec.QTYPE_CNAME
        )
        result = client._parse_response(data)
        self.assertEqual(
            result,
            (0x4, 'example.com', None, codec.RCODE_NOERROR, 'answer_rdlength'),
        )

    def test_parse_response_rejects_wrong_type(self):
        client = self._make_client()
        payload = b'hello'
        data = self._build_response(0x5, 'example.com', codec.QTYPE_A,
                                    payload)
        result = client._parse_response(data)
        self.assertEqual(
            result,
            (0x5, 'example.com', None, codec.RCODE_NOERROR, 'no_matching_answer'),
        )

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
        result = client._parse_response(header + question)
        self.assertEqual(
            result,
            (0x6, 'example.com', None, codec.RCODE_NOERROR, 'no_answer'),
        )

    def test_parse_response_invalid_question(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x7,
            codec.FLAG_QR | codec.FLAG_AA,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = b'\xc0\xff' + struct.pack('>HH', codec.QTYPE_A,
                                             codec.QCLASS_IN)
        result = client._parse_response(header + question)
        self.assertEqual(result, (0x7, None, None, None, 'question_parse'))

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

    def test_send_tracks_pending(self):
        client = self._make_send_client()
        permit = client.reserve_send(now=1)
        corr_id = client.send(b'test', permit)
        self.assertEqual(corr_id, 0)
        pending = client._pending.get(corr_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.dns_id, 1)
        self.assertEqual(pending.qname, 'q.example.com')
        self.assertEqual(client._dns_to_corr, {1: 0})
        self.assertEqual(client._sock.sent[0][0], b'packet')
        self.assertEqual(client._sock.sent[0][1], ('127.0.0.1', 53))

    def test_send_rejects_oversize(self):
        client = self._make_send_client(send_mtu=3)
        permit = client.reserve_send(now=1)
        with self.assertRaises(TransportError):
            client.send(b'toolong', permit)
        self.assertEqual(client._sock.sent, [])

    def test_try_recv_success(self):
        payload = b'hello'
        data = self._build_response(0x10, 'q.example.com', codec.QTYPE_CNAME,
                                    payload)
        client = self._make_recv_client(data, 0x10, 'q.example.com')
        result = client._try_recv()
        self.assertEqual(result, (7, payload))
        self.assertIsNone(client._pending.get(7))
        self.assertEqual(client._dns_to_corr, {})

    def test_try_recv_qname_mismatch(self):
        payload = b'hello'
        data = self._build_response(0x11, 'q.example.com', codec.QTYPE_CNAME,
                                    payload)
        client = self._make_recv_client(data, 0x11, 'other.example.com')
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNotNone(client._pending.get(7))
        self.assertEqual(client._dns_to_corr, {0x11: 7})

    def test_try_recv_error_response_cleans_pending(self):
        data = self._build_response(0x12, 'q.example.com', codec.QTYPE_CNAME,
                                    b'ignored', flags_extra=codec.RCODE_NXDOMAIN)
        client = self._make_recv_client(data, 0x12, 'q.example.com')
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNone(client._pending.get(7))
        self.assertEqual(client._dns_to_corr, {})

    def test_close_clears_pending(self):
        client = DnsClient.__new__(DnsClient)
        client._pending = PendingTracker(1.0)
        client._pending.add(1, _PendingQuery(2, 'q.example.com'), now=0)
        client._dns_to_corr = {2: 1}
        client._sock = DummyCloseSock()
        client.close()
        self.assertEqual(len(client._pending), 0)
        self.assertEqual(client._dns_to_corr, {})
        self.assertIsNone(client._sock)


if __name__ == '__main__':
    unittest.main()
