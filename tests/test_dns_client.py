# -*- coding: ascii -*-
"""Tests for DNS client transport."""

from __future__ import absolute_import

import select
import socket
import struct
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.transport.dns import codec
import sfb.transport.dns.dns_client as dns_client
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


class FailingSock(object):
    def sendto(self, data, addr):
        raise socket.error('fail')


class FailingRecvSock(object):
    def recvfrom(self, bufsize):
        raise socket.error('fail')


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

    def _make_send_client(self, send_packet_mtu=1024):
        client = DnsClient.__new__(DnsClient)
        client._max_in_flight = 5
        client._send_packet_mtu = send_packet_mtu
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

    def _build_answer(self, name, rtype, rclass, rdata):
        answer = codec.encode_name(name)
        answer += struct.pack('>HHIH', rtype, rclass, 0, len(rdata))
        answer += rdata
        return answer

    def _build_response_with_answers(self, query_id, qname, answers):
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            len(answers),  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = codec.encode_name(qname)
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        return header + question + b''.join(answers)

    def _make_capacity_client(self, max_in_flight=1):
        client = DnsClient.__new__(DnsClient)
        client._max_in_flight = max_in_flight
        client._pending = PendingTracker(100.0)
        client._dns_to_corr = {}
        return client

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

    def test_init_parses_resolver_with_port_and_edns(self):
        config = Config()
        config.dns_base_domain = 'Example.COM'
        config.dns_resolver = '1.2.3.4:5353'
        config.dns_edns_size = 4096
        config.dns_recv_bufsize_min = 2048
        client = DnsClient(config)
        try:
            self.assertEqual(client._base_domain, 'example.com')
            self.assertEqual(client._resolver, ('1.2.3.4', 5353))
            self.assertEqual(client._opt_arcount, 1)
            self.assertTrue(client._opt_record)
            self.assertIsNone(client._payload_cap)
            self.assertEqual(
                client._send_packet_mtu,
                codec.calc_query_mtu(client._base_domain,
                                     client._label_max_len),
            )
            self.assertEqual(
                client._recv_packet_mtu,
                codec.calc_response_mtu(client._rtype,
                                        config.dns_edns_size,
                                        client._cname_suffix,
                                        client._label_max_len),
            )
            self.assertEqual(client._recv_bufsize, 4096)
        finally:
            client.close()

    def test_init_uses_default_resolver_port(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = '8.8.8.8'
        client = DnsClient(config)
        try:
            self.assertEqual(client._resolver, ('8.8.8.8', 53))
        finally:
            client.close()

    def test_init_uses_system_resolver(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = None
        original = dns_client.load_system_resolvers
        dns_client.load_system_resolvers = lambda: [('9.9.9.9', 53)]
        try:
            client = DnsClient(config)
            try:
                self.assertEqual(client._resolver, ('9.9.9.9', 53))
            finally:
                client.close()
        finally:
            dns_client.load_system_resolvers = original

    def test_init_no_system_resolvers_raises(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = None
        original = dns_client.load_system_resolvers
        dns_client.load_system_resolvers = lambda: []
        try:
            with self.assertRaises(TransportError):
                DnsClient(config)
        finally:
            dns_client.load_system_resolvers = original

    def test_init_sets_payload_cap_without_edns(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_edns_size = 512
        config.dns_recv_bufsize_min = 128
        config.dns_resolver = '1.1.1.1'
        client = DnsClient(config)
        try:
            expected = codec.calc_cname_payload_cap(
                client._base_domain,
                client._cname_suffix,
                client._label_max_len,
                client._edns_size,
            )
            self.assertEqual(client._payload_cap, expected)
            self.assertEqual(client._opt_arcount, 0)
            self.assertEqual(client._opt_record, b'')
            self.assertEqual(client._recv_bufsize, 512)
        finally:
            client.close()

    def test_init_rejects_non_config(self):
        with self.assertRaises(TypeError):
            DnsClient(object())

    def test_init_rejects_unknown_qtype(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = '1.1.1.1'
        config.dns_query_type = 'NOPE'
        with self.assertRaises(KeyError):
            DnsClient(config)

    def test_init_rejects_unknown_rtype(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = '1.1.1.1'
        config.dns_response_type = 'NOPE'
        with self.assertRaises(KeyError):
            DnsClient(config)

    def test_init_rejects_invalid_resolver_port(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_resolver = '1.2.3.4:bad'
        with self.assertRaises(ValueError):
            DnsClient(config)

    def test_init_uses_min_recv_bufsize(self):
        config = Config()
        config.dns_base_domain = 'example.com'
        config.dns_edns_size = 512
        config.dns_recv_bufsize_min = 2048
        config.dns_resolver = '1.1.1.1'
        client = DnsClient(config)
        try:
            self.assertEqual(client._recv_bufsize, 2048)
        finally:
            client.close()

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

    def test_parse_response_skips_extra_questions(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x17,
            codec.FLAG_QR | codec.FLAG_AA,
            2,  # QDCOUNT
            0,  # ANCOUNT
            0,
            0,
        )
        question1 = codec.encode_name('example.com')
        question1 += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        question2 = codec.encode_name('other.example.com')
        question2 += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        result = client._parse_response(header + question1 + question2)
        self.assertEqual(
            result,
            (0x17, 'example.com', None, codec.RCODE_NOERROR, 'no_answer'),
        )

    def test_parse_response_no_question(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x8,
            codec.FLAG_QR | codec.FLAG_AA,
            0,  # QDCOUNT
            0,  # ANCOUNT
            0,
            0,
        )
        result = client._parse_response(header)
        self.assertEqual(result, (0x8, None, None, None, 'question_parse'))

    def test_parse_response_rejects_answer_name(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0x9,
            codec.FLAG_QR | codec.FLAG_AA,
            1,
            1,
            0,
            0,
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        answer = b'\xc0\xff'
        result = client._parse_response(header + question + answer)
        self.assertEqual(
            result,
            (0x9, 'example.com', None, codec.RCODE_NOERROR, 'answer_name'),
        )

    def test_parse_response_rejects_answer_header(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0xA,
            codec.FLAG_QR | codec.FLAG_AA,
            1,
            1,
            0,
            0,
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        answer = b'\x00'
        result = client._parse_response(header + question + answer)
        self.assertEqual(
            result,
            (0xA, 'example.com', None, codec.RCODE_NOERROR, 'answer_header'),
        )

    def test_parse_response_rejects_cname_decode(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0xB,
            codec.FLAG_QR | codec.FLAG_AA,
            1,
            1,
            0,
            0,
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        answer = self._build_answer(
            'example.com',
            codec.QTYPE_CNAME,
            codec.QCLASS_IN,
            b'\xff',
        )
        result = client._parse_response(header + question + answer)
        self.assertEqual(
            result,
            (0xB, 'example.com', None, codec.RCODE_NOERROR, 'cname_decode'),
        )

    def test_parse_response_rejects_cname_rdlength(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0xC,
            codec.FLAG_QR | codec.FLAG_AA,
            1,
            1,
            0,
            0,
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        rdata = codec.encode_name('c.example.com')
        answer = codec.encode_name('example.com')
        answer += struct.pack('>HHIH',
            codec.QTYPE_CNAME,
            codec.QCLASS_IN,
            0,
            len(rdata) - 1,
        )
        answer += rdata
        result = client._parse_response(header + question + answer)
        self.assertEqual(
            result,
            (0xC, 'example.com', None, codec.RCODE_NOERROR, 'cname_rdlength'),
        )

    def test_parse_response_rejects_payload_decode(self):
        client = self._make_client()
        header = struct.pack('>HHHHHH',
            0xD,
            codec.FLAG_QR | codec.FLAG_AA,
            1,
            1,
            0,
            0,
        )
        question = codec.encode_name('example.com')
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        rdata = codec.encode_name('c.example.com')
        answer = codec.encode_name('example.com')
        answer += struct.pack('>HHIH',
            codec.QTYPE_CNAME,
            codec.QCLASS_IN,
            0,
            len(rdata),
        )
        answer += rdata
        result = client._parse_response(header + question + answer)
        self.assertEqual(
            result,
            (0xD, 'example.com', None, codec.RCODE_NOERROR, 'payload_decode'),
        )

    def test_parse_response_uses_late_matching_answer(self):
        client = self._make_client()
        payload = b'hello'
        target = codec.encode_cname_target(payload, 'c.example.com')
        rdata = codec.encode_name(target)
        answer_a = self._build_answer(
            'example.com',
            codec.QTYPE_A,
            codec.QCLASS_IN,
            b'',
        )
        answer_cname = self._build_answer(
            'example.com',
            codec.QTYPE_CNAME,
            codec.QCLASS_IN,
            rdata,
        )
        data = self._build_response_with_answers(
            0xE,
            'example.com',
            [answer_a, answer_cname],
        )
        result = client._parse_response(data)
        self.assertEqual(
            result,
            (0xE, 'example.com', payload, codec.RCODE_NOERROR, 'ok'),
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

    def test_reserve_send_blocks_when_pending_full(self):
        client = self._make_capacity_client(max_in_flight=1)
        client._pending.add(1, _PendingQuery(2, 'q.example.com'), now=0)
        permit = client.reserve_send(now=0)
        self.assertIsNone(permit)

    def test_reserve_send_blocks_when_reserved_full(self):
        client = self._make_capacity_client(max_in_flight=1)
        permit = client.reserve_send(now=0)
        self.assertIsNotNone(permit)
        self.assertIsNone(client.reserve_send(now=0))

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
        client = self._make_send_client(send_packet_mtu=3)
        permit = client.reserve_send(now=1)
        with self.assertRaises(TransportError):
            client.send(b'toolong', permit)
        self.assertEqual(client._sock.sent, [])

    def test_send_rejects_text_input(self):
        client = self._make_send_client()
        permit = client.reserve_send(now=1)
        with self.assertRaises(TypeError):
            client.send(u'test', permit)
        self.assertEqual(client._sock.sent, [])

    def test_send_raises_on_socket_error(self):
        client = self._make_send_client()
        client._sock = FailingSock()
        permit = client.reserve_send(now=1)
        with self.assertRaises(TransportError):
            client.send(b'test', permit)

    def test_send_uses_pending_count_when_unset(self):
        client = self._make_send_client()
        client._pending.add(8, _PendingQuery(9, 'q.example.com'), now=0)
        permit = client._reserve_permit(now=1)
        corr_id = client.send(b'test', permit)
        self.assertEqual(corr_id, 0)
        self.assertIsNotNone(client._pending.get(corr_id))

    def test_recv_non_blocking_timeout(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}

        def fake_select(rlist, wlist, xlist, timeout):
            return ([], [], [])

        def fail_try_recv():
            raise AssertionError('unexpected _try_recv')

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        client._try_recv = fail_try_recv
        try:
            self.assertEqual(client.recv(timeout=0), (None, None))
        finally:
            dns_client.select.select = original_select

    def test_recv_non_blocking_ready(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        called = []

        def fake_select(rlist, wlist, xlist, timeout):
            self.assertEqual(timeout, 0)
            return (rlist, [], [])

        def fake_try_recv():
            called.append(True)
            return (1, b'ok')

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        client._try_recv = fake_try_recv
        try:
            self.assertEqual(client.recv(timeout=0), (1, b'ok'))
            self.assertEqual(len(called), 1)
        finally:
            dns_client.select.select = original_select

    def test_recv_blocking_waits_with_none(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        waits = []

        def fake_select(rlist, wlist, xlist, timeout):
            waits.append(timeout)
            return (rlist, [], [])

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        client._try_recv = lambda: (1, b'ok')
        try:
            self.assertEqual(client.recv(timeout=None), (1, b'ok'))
            self.assertEqual(waits, [None])
        finally:
            dns_client.select.select = original_select

    def test_recv_timeout_expired_before_select(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}

        def fail_select(rlist, wlist, xlist, timeout):
            raise AssertionError('unexpected select')

        times = [0.0, 100.0, 200.0]

        def fake_now():
            if times:
                return times.pop(0)
            return 200.0

        original_select = dns_client.select.select
        dns_client.select.select = fail_select
        time_provider.set_time_source(fake_now, clamp=False)
        try:
            self.assertEqual(client.recv(timeout=1.0), (None, None))
        finally:
            time_provider.reset_time_source()
            dns_client.select.select = original_select

    def test_recv_select_error_raises(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}

        def fake_select(rlist, wlist, xlist, timeout):
            raise select.error('boom')

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        try:
            with self.assertRaises(TransportError):
                client.recv(timeout=0)
        finally:
            dns_client.select.select = original_select

    def test_recv_timeout_select_empty(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}

        def fake_select(rlist, wlist, xlist, timeout):
            return ([], [], [])

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        try:
            self.assertEqual(client.recv(timeout=1.0), (None, None))
        finally:
            dns_client.select.select = original_select

    def test_recv_retries_after_empty_result(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        calls = []
        results = [(None, None), (2, b'ok')]

        def fake_select(rlist, wlist, xlist, timeout):
            calls.append(timeout)
            return (rlist, [], [])

        def fake_try_recv():
            return results.pop(0)

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        client._try_recv = fake_try_recv
        try:
            self.assertEqual(client.recv(timeout=None), (2, b'ok'))
            self.assertEqual(len(calls), 2)
        finally:
            dns_client.select.select = original_select

    def test_recv_timed_retries_until_deadline(self):
        client = DnsClient.__new__(DnsClient)
        client._sock = object()
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        calls = []
        results = [(None, None)]

        def fake_select(rlist, wlist, xlist, timeout):
            calls.append(timeout)
            return (rlist, [], [])

        def fake_try_recv():
            if results:
                return results.pop(0)
            return (None, None)

        times = [0.0, 0.4, 1.2]

        def fake_now():
            if times:
                return times.pop(0)
            return 1.2

        original_select = dns_client.select.select
        dns_client.select.select = fake_select
        client._try_recv = fake_try_recv
        time_provider.set_time_source(fake_now, clamp=False)
        try:
            self.assertEqual(client.recv(timeout=1.0), (None, None))
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0] <= 1.0)
        finally:
            time_provider.reset_time_source()
            dns_client.select.select = original_select

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

    def test_try_recv_malformed_response(self):
        client = self._make_client()
        client._sock = DummyRecvSock(b'\x00')
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._pending.add(1, _PendingQuery(2, 'q.example.com'), now=0)
        client._dns_to_corr = {2: 1}
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNotNone(client._pending.get(1))
        self.assertEqual(client._dns_to_corr, {2: 1})

    def test_try_recv_stale_response_ignored(self):
        payload = b'hello'
        data = self._build_response(0x13, 'q.example.com', codec.QTYPE_CNAME,
                                    payload)
        client = self._make_client()
        client._sock = DummyRecvSock(data)
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._pending.add(4, _PendingQuery(5, 'q.example.com'), now=0)
        client._dns_to_corr = {5: 4}
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNotNone(client._pending.get(4))
        self.assertEqual(client._dns_to_corr, {5: 4})

    def test_try_recv_missing_pending_entry(self):
        payload = b'hello'
        data = self._build_response(0x14, 'q.example.com', codec.QTYPE_CNAME,
                                    payload)
        client = self._make_client()
        client._sock = DummyRecvSock(data)
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {0x14: 7}
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertEqual(client._dns_to_corr, {0x14: 7})

    def test_try_recv_not_response_cleans_pending(self):
        header = struct.pack('>HHHHHH', 0x15, 0, 0, 0, 0, 0)
        client = self._make_client()
        client._sock = DummyRecvSock(header)
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._pending.add(7, _PendingQuery(0x15, 'q.example.com'), now=0)
        client._dns_to_corr = {0x15: 7}
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNone(client._pending.get(7))
        self.assertEqual(client._dns_to_corr, {})

    def test_try_recv_payload_none_cleans_pending(self):
        data = self._build_response(0x16, 'q.example.com', codec.QTYPE_A,
                                    b'ignored')
        client = self._make_recv_client(data, 0x16, 'q.example.com')
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertIsNone(client._pending.get(7))
        self.assertEqual(client._dns_to_corr, {})

    def test_try_recv_socket_error_raises(self):
        client = self._make_client()
        client._sock = FailingRecvSock()
        client._recv_bufsize = 512
        client._pending = PendingTracker(1.0)
        client._dns_to_corr = {}
        with self.assertRaises(TransportError):
            client._try_recv()

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
