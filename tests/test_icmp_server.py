# -*- coding: ascii -*-
from __future__ import absolute_import

import socket
import unittest

from sfb.transport.icmp import icmp_server
from sfb.transport.icmp.icmp_packet import build_echo_reply, build_echo_request
from sfb.transport.icmp.icmp_server import IcmpServer
from sfb.transport.transport_base import TransportError


class QueueSock(object):
    def __init__(self, packets=None, recv_error=None, send_error=None):
        self._packets = list(packets or [])
        self._recv_error = recv_error
        self._send_error = send_error
        self.sent = []

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

    def has_packets(self):
        return bool(self._packets)


class IcmpServerTests(unittest.TestCase):
    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, original)
        return original

    def _select_for_sock(self, sock):
        def fake_select(read_list, write_list, exc_list, timeout):
            if sock.has_packets():
                return ([sock], [], [])
            return ([], [], [])
        return fake_select

    def _make_server(self):
        server = IcmpServer.__new__(IcmpServer)
        server._recv_packet_mtu = 4
        server._send_packet_mtu = 4
        server._recv_bufsize = 65535
        return server

    def test_recv_logs_malformed_request(self):
        server = self._make_server()
        packet = build_echo_reply(1, 2, b'hi')
        sock = QueueSock(packets=[(packet, ('10.0.0.1', 0))])
        server._sock = sock
        self._patch(icmp_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.malformed_request':
                calls.append(fields())

        self._patch(icmp_server, 'log_event', fake_log_event)
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['reason'], 'type_mismatch')

    def test_recv_logs_oversize_request(self):
        server = self._make_server()
        server._recv_packet_mtu = 2
        packet = build_echo_request(1, 3, b'toolong')
        sock = QueueSock(packets=[(packet, ('10.0.0.2', 0))])
        server._sock = sock
        self._patch(icmp_server.select, 'select', self._select_for_sock(sock))
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.oversize_request':
                calls.append(fields())

        self._patch(icmp_server, 'log_event', fake_log_event)
        data, responder = server.recv(timeout=0)
        self.assertIsNone(data)
        self.assertIsNone(responder)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['recv_packet_mtu'], 2)

    def test_responder_logs_send_oversize(self):
        server = self._make_server()
        server._sock = QueueSock()
        responder = server._make_responder(('10.0.0.3', 0), 5, 6)
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.send_oversize':
                calls.append(fields())

        self._patch(icmp_server, 'log_event', fake_log_event)
        with self.assertRaises(TransportError):
            responder(b'toolong')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['send_packet_mtu'], 4)

    def test_responder_logs_send_failed(self):
        server = self._make_server()
        server._sock = QueueSock(send_error=socket.error('boom'))
        responder = server._make_responder(('10.0.0.4', 0), 7, 8)
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.send_failed':
                calls.append(fields())

        self._patch(icmp_server, 'log_event', fake_log_event)
        with self.assertRaises(TransportError):
            responder(b'ok')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['corr_id'], 8)


if __name__ == '__main__':
    unittest.main()
