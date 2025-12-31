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
        corr_id = client.send(b'hello')
        data, responder = server.recv(timeout=0)
        self.assertEqual(data, b'hello')
        responder(b'world')
        self.assertEqual(client.recv(timeout=0.1), (corr_id, b'world'))

    def test_pending_limit(self):
        client, server = create_inmemory_transport_pair(Config(), max_pending=1)
        client.send(b'a')
        with self.assertRaises(TransportError):
            client.send(b'b')
        data, responder = server.recv(timeout=0)
        responder(b'a')
        self.assertEqual(client.recv(timeout=0.1)[1], b'a')

    def test_mtu_enforced(self):
        client, server = create_inmemory_transport_pair(
            Config(), send_mtu=4, recv_mtu=4
        )
        with self.assertRaises(TransportError):
            client.send(b'12345')
        corr_id = client.send(b'1234')
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
            client.send(b'data')


if __name__ == '__main__':
    unittest.main()
