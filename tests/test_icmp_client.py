# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.transport.icmp.icmp_client import IcmpClient
from sfb.transport.transport_base import PendingTracker


class DummySock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        return len(data)


class IcmpClientTests(unittest.TestCase):
    def _make_client(self):
        client = IcmpClient.__new__(IcmpClient)
        client._send_mtu = 1024
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


if __name__ == '__main__':
    unittest.main()
