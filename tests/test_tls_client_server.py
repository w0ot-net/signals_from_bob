# -*- coding: ascii -*-
from __future__ import absolute_import

import errno
import socket
import time
import unittest

from sfb.config import Config
from sfb.transport.transport_base import PendingTracker, TransportError
from sfb.transport.tls.config import validate_tls_config
from sfb.transport.tls.tls_client import TlsClient, _PendingConn
from sfb.transport.tls.tls_server import TlsServer
from sfb import time_provider


def _get_ephemeral_port():
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
    except OSError as exc:
        if exc.errno in (errno.EPERM, errno.EACCES):
            raise unittest.SkipTest('socket creation not permitted')
        raise
    finally:
        if sock is not None:
            sock.close()


class DummySock(object):
    def close(self):
        pass


class TlsClientServerTests(unittest.TestCase):
    def tearDown(self):
        time_provider.reset_time_source()

    def test_round_trip(self):
        port = _get_ephemeral_port()
        server_cfg = Config(
            transport='tls',
            tls_listen_addr='127.0.0.1:%d' % port,
        )
        server = TlsServer(server_cfg)
        client_cfg = Config(
            transport='tls',
            tls_target='127.0.0.1:%d' % port,
        )
        client = TlsClient(client_cfg)
        try:
            permit = client.reserve_send()
            self.assertIsNotNone(permit)
            corr_id = client.send(b'ping', permit)

            payload = None
            responder = None
            deadline = time.time() + 2.0
            while time.time() < deadline:
                client.recv(timeout=0)
                payload, responder = server.recv(timeout=0.1)
                if payload is not None:
                    break
            self.assertEqual(payload, b'ping')
            self.assertIsNotNone(responder)
            responder(b'pong')
            server.recv(timeout=0)
            recv_id, response = client.recv(timeout=1.0)
            self.assertEqual(recv_id, corr_id)
            self.assertEqual(response, b'pong')
        finally:
            client.close()
            server.close()

    def test_pending_limit(self):
        port = _get_ephemeral_port()
        server = TlsServer(Config(transport='tls',
                                  tls_listen_addr='127.0.0.1:%d' % port,
                                  max_in_flight=1))
        client = TlsClient(Config(transport='tls',
                                  tls_target='127.0.0.1:%d' % port,
                                  max_in_flight=1))
        try:
            permit = client.reserve_send()
            self.assertIsNotNone(permit)
            client.send(b'ping', permit)
            self.assertIsNone(client.reserve_send())
        finally:
            client.close()
            server.close()

    def test_prune_deadlines(self):
        client = TlsClient.__new__(TlsClient)
        client._pending_state = {}
        client._pending = PendingTracker(1.0)
        client._sock_to_corr = {}
        client._reserved = set()
        state = _PendingConn(DummySock(), b'hi', 1.0)
        corr_id = 1
        client._pending_state[corr_id] = state
        client._pending.add(corr_id, True, now=0.0)
        client._sock_to_corr[state.sock] = corr_id
        client._prune_deadlines(now=2.0)
        self.assertEqual(client.pending_count(), 0)

    def test_invalid_timeouts(self):
        cfg = Config(
            transport='tls',
            tls_pending_timeout=1.0,
            tls_connect_timeout=2.0,
            tls_handshake_timeout=2.0,
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')

    def test_invalid_sni_alpn(self):
        cfg = Config(
            transport='tls',
            tls_sni='bad..name',
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')
        cfg = Config(
            transport='tls',
            tls_alpn='h2,,http/1.1',
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')


if __name__ == '__main__':
    unittest.main()
