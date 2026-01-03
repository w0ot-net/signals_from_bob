# -*- coding: ascii -*-
from __future__ import absolute_import

import logging
import select
import socket
import struct
import unittest

from sfb import time_provider
from sfb.config import Config, DNS_STANDARD_SIZE
from sfb.transport.dns import codec
from sfb.transport.dns import dns_server
from sfb.transport.dns.dns_server import DnsServer
from sfb.transport.transport_base import TransportError


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)


class QueueSock(object):
    def __init__(self, packets=None, recv_error=None, send_error=None):
        self._packets = list(packets or [])
        self._recv_error = recv_error
        self._send_error = send_error
        self.sent = []
        self.closed = False

    def recvfrom(self, bufsize):
        if self._recv_error is not None:
            raise self._recv_error
        if not self._packets:
            raise AssertionError('recvfrom called with no packets')
        return self._packets.pop(0)

    def sendto(self, data, addr):
        if self._send_error is not None:
            raise self._send_error
        self.sent.append((data, addr))
        return len(data)

    def close(self):
        self.closed = True

    def has_packets(self):
        return bool(self._packets)


class InitSock(object):
    def __init__(self, bind_error=None):
        self.bind_error = bind_error
        self.bound = None
        self.closed = False
        self.opts = []

    def setsockopt(self, *args):
        self.opts.append(args)

    def bind(self, addr):
        if self.bind_error is not None:
            raise self.bind_error
        self.bound = addr

    def close(self):
        self.closed = True


class DnsServerTests(unittest.TestCase):
    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(lambda: setattr(obj, name, original))
        return original

    def _make_server(self, rtype=codec.QTYPE_CNAME, edns_size=DNS_STANDARD_SIZE):
        server = DnsServer.__new__(DnsServer)
        server._qtype = codec.QTYPE_A
        server._rtype = rtype
        server._edns_size = edns_size
        server._label_max_len = 50
        server._cname_suffix = 'c.example.com'
        server._cname_suffix_lower = server._cname_suffix.lower()
        server._opt_record = b''
        server._opt_arcount = 0
        server._opt_record_len = len(server._opt_record)
        server._sock = DummySock()
        server._logger = logging.getLogger('test')
        server._base_domain = 'example.com'
        server._cname_a_addr_bytes = b'\x7f\x00\x00\x01'
        server._recv_bufsize = edns_size
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

    def _select_for_sock(self, sock):
        def fake_select(read_list, write_list, exc_list, timeout):
            if sock.has_packets():
                return ([sock], [], [])
            return ([], [], [])
        return fake_select

    def test_init_requires_config_type(self):
        with self.assertRaises(TypeError):
            DnsServer(object())

    def test_init_parses_listen_addr_default_port_and_payload_cap(self):
        sockets = []

        def socket_factory(*args, **kwargs):
            sock = InitSock()
            sockets.append(sock)
            return sock

        self._patch(dns_server.socket, 'socket', socket_factory)
        config = Config(
            dns_base_domain='Example.COM.',
            dns_listen_addr='127.0.0.1',
            dns_edns_size=DNS_STANDARD_SIZE,
            dns_recv_bufsize_min=128,
            dns_cname_label='c',
        )
        server = DnsServer(config)
        self.addCleanup(server.close)
        self.assertEqual(server._base_domain, 'example.com')
        self.assertEqual(server._listen_addr, ('127.0.0.1', 53))
        self.assertEqual(sockets[0].bound, ('127.0.0.1', 53))
        self.assertEqual(server._recv_bufsize, DNS_STANDARD_SIZE)
        self.assertEqual(server._opt_arcount, 0)
        self.assertEqual(server._opt_record, b'')
        self.assertIsNotNone(server._payload_cap)

    def test_init_parses_listen_addr_with_port_and_edns_opt(self):
        sockets = []

        def socket_factory(*args, **kwargs):
            sock = InitSock()
            sockets.append(sock)
            return sock

        self._patch(dns_server.socket, 'socket', socket_factory)
        config = Config(
            dns_base_domain='example.com',
            dns_listen_addr='127.0.0.1:5353',
            dns_edns_size=1232,
            dns_cname_a_addr='bad_addr',
        )
        server = DnsServer(config)
        self.addCleanup(server.close)
        self.assertEqual(server._listen_addr, ('127.0.0.1', 5353))
        self.assertEqual(sockets[0].bound, ('127.0.0.1', 5353))
        self.assertEqual(server._recv_bufsize, config.dns_recv_bufsize_min)
        self.assertEqual(server._opt_arcount, 1)
        self.assertGreater(server._opt_record_len, 0)
        self.assertIsNone(server._payload_cap)
        self.assertEqual(server._cname_a_addr_bytes, b'\x00\x00\x00\x00')

    def test_init_bind_failure_closes_socket(self):
        bind_error = socket.error('bind failed')
        sock = InitSock(bind_error=bind_error)

        def socket_factory(*args, **kwargs):
            return sock

        self._patch(dns_server.socket, 'socket', socket_factory)
        config = Config(dns_base_domain='example.com')
        server = DnsServer.__new__(DnsServer)
        with self.assertRaises(socket.error):
            DnsServer.__init__(server, config)
        self.assertTrue(sock.closed)
        self.assertIsNone(server._sock)

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

    def test_parse_query_accepts_compression_pointer(self):
        server = self._make_server()
        # Header bytes encode "a.com" at offset 0 for compression pointer use.
        header = struct.pack('>HHHHHH', 0x0161, 0x0363, 0x6f6d, 1, 0, 0)
        question = b'\x06tunnel\xc0\x00'
        question += struct.pack('>HH', codec.QTYPE_A, codec.QCLASS_IN)
        data = header + question
        query_id, qname, qtype = server._parse_query(data)
        self.assertEqual(query_id, 0x0161)
        self.assertEqual(qname, 'tunnel.a.com')
        self.assertEqual(qtype, codec.QTYPE_A)

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

    def test_parse_query_rejects_short_header(self):
        server = self._make_server()
        with self.assertRaises(ValueError):
            server._parse_query(b'\x00' * 11)

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

    def test_send_response_send_error(self):
        server = self._make_server()
        server._sock = QueueSock(send_error=socket.error('boom'))
        with self.assertRaises(TransportError):
            server._send_response(
                0x13,
                'tunnel.example.com',
                codec.QTYPE_A,
                b'hi',
                ('127.0.0.1', 5353),
                payload_cap=None,
                qname_wire_len=None,
                max_packet_size=None,
            )

    def test_send_response_includes_edns_opt(self):
        server = self._make_server(edns_size=1232)
        server._edns_size = 1232
        server._opt_record = codec.build_opt_record(server._edns_size)
        server._opt_arcount = 1
        server._opt_record_len = len(server._opt_record)
        server._send_response(
            0x14,
            'tunnel.example.com',
            codec.QTYPE_A,
            b'hi',
            ('127.0.0.1', 5353),
            payload_cap=None,
            qname_wire_len=None,
            max_packet_size=None,
        )
        response, _ = server._sock.sent[0]
        _, _, _, _, _, arcount = struct.unpack('>HHHHHH', response[:12])
        self.assertEqual(arcount, 1)
        self.assertGreater(len(server._opt_record), 0)
        self.assertTrue(response.endswith(server._opt_record))

    def test_send_response_logs_oversize(self):
        server = self._make_server()
        calls = []

        def fake_log_event(logger, level, event, message, data_func):
            if event == 'dns.send':
                calls.append(data_func())

        self._patch(dns_server, 'log_event', fake_log_event)
        server._send_response(
            0x15,
            'tunnel.example.com',
            codec.QTYPE_A,
            b'hi',
            ('127.0.0.1', 5353),
            payload_cap=1,
            qname_wire_len=1,
            max_packet_size=20,
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]['oversize'])
        self.assertEqual(calls[0]['max_packet_size'], 20)
        self.assertGreater(calls[0]['bytes'], 20)

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

        rdata_start = offset + 10
        rdata = response[rdata_start:rdata_start + rdlength]
        mname, rdata_offset = codec.decode_name(rdata, 0)
        rname, rdata_offset = codec.decode_name(rdata, rdata_offset)
        serial, refresh, retry, expire, minimum = struct.unpack(
            '>IIIII', rdata[rdata_offset:rdata_offset + 20]
        )
        self.assertEqual(mname, 'ns.' + server._base_domain)
        self.assertEqual(rname, 'hostmaster.' + server._base_domain)
        self.assertEqual((serial, refresh, retry, expire, minimum),
                         (1, 0, 0, 0, 0))
        self.assertEqual(rdata_offset + 20, len(rdata))

    def test_send_empty_response_send_error(self):
        server = self._make_server()
        server._sock = QueueSock(send_error=socket.error('boom'))
        with self.assertRaises(TransportError):
            server._send_empty_response(
                0x22,
                'tunnel.example.com',
                codec.QTYPE_A,
                ('127.0.0.1', 5353),
                reason='test',
            )

    def test_send_empty_response_includes_edns_opt(self):
        server = self._make_server(edns_size=1232)
        server._edns_size = 1232
        server._opt_record = codec.build_opt_record(server._edns_size)
        server._opt_arcount = 1
        server._opt_record_len = len(server._opt_record)
        server._send_empty_response(
            0x24,
            'tunnel.example.com',
            codec.QTYPE_A,
            ('127.0.0.1', 5353),
            reason='test',
        )
        response, _ = server._sock.sent[0]
        _, _, _, _, _, arcount = struct.unpack('>HHHHHH', response[:12])
        self.assertEqual(arcount, 1)
        self.assertGreater(len(server._opt_record), 0)
        self.assertTrue(response.endswith(server._opt_record))

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

    def test_send_cname_followup_send_error(self):
        server = self._make_server()
        server._sock = QueueSock(send_error=socket.error('boom'))
        with self.assertRaises(TransportError):
            server._send_cname_followup(
                0x23,
                'c.example.com',
                codec.QTYPE_A,
                ('127.0.0.1', 5353),
            )

    def test_send_cname_followup_includes_edns_opt(self):
        server = self._make_server(edns_size=1232)
        server._edns_size = 1232
        server._opt_record = codec.build_opt_record(server._edns_size)
        server._opt_arcount = 1
        server._opt_record_len = len(server._opt_record)
        server._send_cname_followup(
            0x25,
            'c.example.com',
            codec.QTYPE_A,
            ('127.0.0.1', 5353),
        )
        response, _ = server._sock.sent[0]
        _, _, _, _, _, arcount = struct.unpack('>HHHHHH', response[:12])
        self.assertEqual(arcount, 1)
        self.assertGreater(len(server._opt_record), 0)
        self.assertTrue(response.endswith(server._opt_record))

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

    def test_response_payload_cap_clamps_small_edns(self):
        server = self._make_server()
        server._edns_size = 200
        payload_cap, qname_wire_len, max_packet_size = (
            server._response_payload_cap('tunnel.example.com')
        )
        self.assertEqual(max_packet_size, DNS_STANDARD_SIZE)
        self.assertEqual(qname_wire_len,
                         len(codec.encode_name('tunnel.example.com')))
        self.assertGreaterEqual(payload_cap, 0)

    def test_response_payload_cap_handles_encode_error(self):
        server = self._make_server()
        original = codec.encode_cname_target

        def bad_encode(data, cname_suffix, label_max_len):
            if len(data) > 0:
                raise ValueError('boom')
            return original(data, cname_suffix, label_max_len)

        self._patch(codec, 'encode_cname_target', bad_encode)
        payload_cap, qname_wire_len, max_packet_size = (
            server._response_payload_cap('tunnel.example.com')
        )
        self.assertEqual(payload_cap, 0)
        self.assertGreater(qname_wire_len, 0)
        self.assertEqual(max_packet_size, DNS_STANDARD_SIZE)

    def test_recv_timeout_deadline_returns_none(self):
        server = self._make_server()
        server._sock = QueueSock()
        times = [0.0, 2.0]

        def now():
            return times.pop(0)

        time_provider.set_time_source(now, clamp=False)
        self.addCleanup(time_provider.reset_time_source)
        data, responder = server.recv(timeout=1)
        self.assertIsNone(data)
        self.assertIsNone(responder)

    def test_recv_timeout_none_uses_blocking_select(self):
        server = self._make_server()
        server._sock = QueueSock()
        waits = []

        def fake_select(read_list, write_list, exc_list, timeout):
            waits.append(timeout)
            return ([], [], [])

        self._patch(dns_server.select, 'select', fake_select)
        data, responder = server.recv(timeout=None)
        self.assertEqual(waits, [None])
        self.assertIsNone(data)
        self.assertIsNone(responder)

    def test_recv_timeout_zero_returns_none(self):
        server = self._make_server()
        server._sock = QueueSock()
        waits = []

        def fake_select(read_list, write_list, exc_list, timeout):
            waits.append(timeout)
            return ([], [], [])

        self._patch(dns_server.select, 'select', fake_select)
        data, responder = server.recv(timeout=0)
        self.assertEqual(waits, [0])
        self.assertIsNone(data)
        self.assertIsNone(responder)

    def test_recv_select_error_raises_transport_error(self):
        server = self._make_server()
        server._sock = QueueSock()

        def fake_select(read_list, write_list, exc_list, timeout):
            raise select.error('boom')

        self._patch(dns_server.select, 'select', fake_select)
        with self.assertRaises(TransportError):
            server.recv(timeout=1)

    def test_recv_socket_error_raises_transport_error(self):
        server = self._make_server()
        sock = QueueSock(recv_error=socket.error('boom'))
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        with self.assertRaises(TransportError):
            server.recv(timeout=1)

    def test_recv_ignores_non_domain(self):
        server = self._make_server()
        packet = self._build_query(0x40, 'other.com', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)

    def test_recv_domain_case_insensitive(self):
        server = self._make_server()
        packet = self._build_query(0x45, 'TuNnEl.ExAmPlE.cOm', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        self._patch(codec, 'decode_query_name',
                    lambda *args, **kwargs: b'data')
        server._response_payload_cap = lambda qname: (1, 2, 3)
        data, responder = server.recv(timeout=0)
        self.assertEqual(data, b'data')
        self.assertTrue(callable(responder))

    def test_recv_base_domain_sends_empty(self):
        server = self._make_server()
        packet = self._build_query(0x46, 'example.com', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_empty(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_empty_response = fake_send_empty
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('reason'), 'decode_failed')

    def test_recv_nonce_only_sends_empty(self):
        server = self._make_server()
        packet = self._build_query(0x47, 'nonce.example.com', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_empty(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_empty_response = fake_send_empty
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('reason'), 'decode_failed')

    def test_recv_label_too_long_sends_empty(self):
        server = self._make_server()
        long_label = 'a' * (server._label_max_len + 1)
        qname = 'abcd.' + long_label + '.example.com'
        packet = self._build_query(0x49, qname, codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_empty(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_empty_response = fake_send_empty
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('reason'), 'decode_failed')

    def test_recv_qtype_mismatch_sends_empty(self):
        server = self._make_server()
        packet = self._build_query(0x41, 'tunnel.example.com',
                                   codec.QTYPE_TXT)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_empty(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_empty_response = fake_send_empty
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('reason'), 'qtype_mismatch')

    def test_recv_cname_followup_sends_a_record(self):
        server = self._make_server()
        packet = self._build_query(0x42, 'c.example.com', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_followup(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_cname_followup = fake_followup
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], 'c.example.com')

    def test_recv_cname_followup_subdomain_sends_a_record(self):
        server = self._make_server()
        packet = self._build_query(0x48, 'sub.c.example.com', codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_followup(*args, **kwargs):
            calls.append((args, kwargs))

        server._send_cname_followup = fake_followup
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], 'sub.c.example.com')

    def test_recv_decode_failed_sends_empty(self):
        server = self._make_server()
        packet = self._build_query(0x43, 'tunnel.example.com',
                                   codec.QTYPE_A)
        sock = QueueSock([(packet, ('127.0.0.1', 5353))])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_empty(*args, **kwargs):
            calls.append((args, kwargs))

        def decode_fail(*args, **kwargs):
            raise ValueError('bad')

        server._send_empty_response = fake_send_empty
        self._patch(codec, 'decode_query_name', decode_fail)
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('reason'), 'decode_failed')

    def test_recv_returns_data_and_responder(self):
        server = self._make_server()
        bad_packet = b'\x00'
        good_packet = self._build_query(0x44, 'tunnel.example.com',
                                        codec.QTYPE_A)
        sock = QueueSock([
            (bad_packet, ('127.0.0.1', 5353)),
            (good_packet, ('127.0.0.1', 5353)),
        ])
        server._sock = sock
        self._patch(dns_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_send_response(*args, **kwargs):
            calls.append((args, kwargs))

        def fake_payload_cap(qname):
            return 7, 9, 512

        def decode_ok(*args, **kwargs):
            return b'data'

        server._send_response = fake_send_response
        server._response_payload_cap = fake_payload_cap
        self._patch(codec, 'decode_query_name', decode_ok)
        data, responder = server.recv(timeout=0)
        self.assertEqual(data, b'data')
        self.assertTrue(callable(responder))
        responder(b'payload')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], 0x44)
        self.assertEqual(calls[0][1]['payload_cap'], 7)
        self.assertEqual(calls[0][1]['qname_wire_len'], 9)
        self.assertEqual(calls[0][1]['max_packet_size'], 512)

    def test_close_closes_socket(self):
        server = self._make_server()
        sock = QueueSock()
        server._sock = sock
        server.close()
        self.assertTrue(sock.closed)
        self.assertIsNone(server._sock)


if __name__ == '__main__':
    unittest.main()
