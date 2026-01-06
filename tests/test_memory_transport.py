# -*- coding: ascii -*-
"""Tests for the in-memory transport."""

from __future__ import absolute_import

import unittest

from sfb.config import Config
from sfb.transport import (
    TransportError,
    create_inmemory_transport_pair,
)


class InMemoryTransportTests(unittest.TestCase):
    def test_round_trip(self):
        client, server = create_inmemory_transport_pair(Config())
        permit = client.reserve_send()
        self.assertIsNotNone(permit)
        corr_id = client.send(b'hello', permit)
        data, responder = server.recv(timeout=0)
        self.assertEqual(data, b'hello')
        responder(b'world')
        self.assertEqual(client.recv(timeout=0.1), (corr_id, b'world'))

    def test_pending_limit(self):
        client, server = create_inmemory_transport_pair(
            Config(max_in_flight=1)
        )
        permit = client.reserve_send()
        self.assertIsNotNone(permit)
        client.send(b'a', permit)
        self.assertIsNone(client.reserve_send())
        data, responder = server.recv(timeout=0)
        responder(b'a')
        self.assertEqual(client.recv(timeout=0.1)[1], b'a')

    def test_mtu_enforced(self):
        client, server = create_inmemory_transport_pair(
            Config(), send_packet_mtu=4, recv_packet_mtu=4
        )
        permit = client.reserve_send()
        self.assertIsNotNone(permit)
        with self.assertRaises(TransportError):
            client.send(b'12345', permit)
        permit = client.reserve_send()
        self.assertIsNotNone(permit)
        corr_id = client.send(b'1234', permit)
        data, responder = server.recv(timeout=0)
        with self.assertRaises(TransportError):
            responder(b'56789')
        responder(b'ok')
        self.assertEqual(client.recv(timeout=0.1), (corr_id, b'ok'))

    def test_timeouts_return_none(self):
        client, server = create_inmemory_transport_pair(Config())
        self.assertEqual(client.recv(timeout=0), (None, None))
        self.assertEqual(server.recv(timeout=0), (None, None))

    def test_close_prevents_send(self):
        client, server = create_inmemory_transport_pair(Config())
        client.close()
        with self.assertRaises(TransportError):
            client.reserve_send()


if __name__ == '__main__':
    unittest.main()
