# -*- coding: ascii -*-
from __future__ import absolute_import

import socket
import unittest

from sfb.transport.icmp import icmp_client
from sfb.transport.icmp.icmp_client import IcmpClient
from sfb.transport.icmp.icmp_packet import build_echo_reply, build_echo_request
from sfb.transport.transport_base import PendingTracker, TransportError


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)

class DummyRecvSock(object):
    def __init__(self, packet, addr=('127.0.0.1', 0), error=None):
        self._packet = packet
        self._addr = addr
        self._error = error

    def recvfrom(self, bufsize):
        if self._error is not None:
            raise self._error
        return self._packet, self._addr


class DummyErrorSock(object):
    def sendto(self, data, addr):
        raise socket.error('boom')


class IcmpClientTests(unittest.TestCase):
    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, original)
        return original

    def _make_client(self):
        client = IcmpClient.__new__(IcmpClient)
        client._send_packet_mtu = 1024
        client._recv_packet_mtu = 1024
        client._recv_bufsize = 65535
        client._max_in_flight = 5
        client._pending = PendingTracker(1.0)
        client._target_ip = '127.0.0.1'
        client._sock = DummySock()
        client._icmp_id = 1
        client._next_seq = 0
        return client

    def test_send_prunes_once(self):
        client = self._make_client()
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
        client = self._make_client()
        client._pending.add(1, True, now=0)
        permit = client.reserve_send(now=2)
        self.assertIsNotNone(permit)
        count = client.pending_count()
        self.assertEqual(count, 0)

    def test_try_recv_logs_malformed_response(self):
        client = self._make_client()
        packet = build_echo_request(1, 5, b'test')
        client._sock = DummyRecvSock(packet, ('10.0.0.1', 0))
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.malformed_response':
                calls.append(fields())

        self._patch(icmp_client, 'log_event', fake_log_event)
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['reason'], 'type_mismatch')

    def test_try_recv_logs_oversize_response(self):
        client = self._make_client()
        client._recv_packet_mtu = 2
        packet = build_echo_reply(1, 7, b'toolong')
        client._sock = DummyRecvSock(packet, ('10.0.0.2', 0))
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.oversize_response':
                calls.append(fields())

        self._patch(icmp_client, 'log_event', fake_log_event)
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['recv_packet_mtu'], 2)

    def test_try_recv_logs_missing_pending(self):
        client = self._make_client()
        packet = build_echo_reply(1, 9, b'ok')
        client._sock = DummyRecvSock(packet, ('10.0.0.3', 0))
        calls = []

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.missing_pending':
                calls.append(fields())

        self._patch(icmp_client, 'log_event', fake_log_event)
        result = client._try_recv()
        self.assertEqual(result, (None, None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['corr_id'], 9)

    def test_send_impl_logs_send_failed(self):
        client = self._make_client()
        client._sock = DummyErrorSock()
        client._target_ip = '10.0.0.4'
        calls = []

        class DummyPermit(object):
            def __init__(self):
                self.pending_before = 0
                self.now = 1

        def fake_log_event(logger, level, event, message, fields, **kwargs):
            if event == 'icmp.send_failed':
                calls.append(fields())

        self._patch(icmp_client, 'log_event', fake_log_event)
        with self.assertRaises(TransportError):
            client._send_impl(b'test', DummyPermit())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['target'], '10.0.0.4')


if __name__ == '__main__':
    unittest.main()
