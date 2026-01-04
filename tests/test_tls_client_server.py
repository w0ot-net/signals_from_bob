# -*- coding: ascii -*-
from __future__ import absolute_import

import base64
import errno
import select
import socket
import threading
import time
import unittest

from sfb.config import Config
from sfb.transport.transport_base import PendingTracker, TransportError
from sfb.transport.tls_handshake.tls_handshake_config import validate_tls_config
from sfb.transport.tls_handshake.tls_handshake_client import TlsClient, _PendingConn
from sfb.transport.tls_handshake.tls_handshake_server import TlsServer
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


_PROXY_HEADER_LIMIT = 8192


def _safe_close(sock):
    if sock is None:
        return
    try:
        sock.close()
    except Exception:
        pass


class HttpConnectProxy(object):
    def __init__(self, target_host, target_port, status_code=200, response_delay=0.0):
        self._target_host = target_host
        self._target_port = target_port
        self._status_code = status_code
        self._response_delay = response_delay
        self._listener = None
        self._thread = None
        self._stop_event = threading.Event()
        self._request_event = threading.Event()
        self._client_sock = None
        self._target_sock = None
        self.last_request = None
        self.host = '127.0.0.1'
        self.port = None

    def start(self):
        try:
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listener.bind((self.host, 0))
            self._listener.listen(1)
            self._listener.settimeout(0.1)
            self.port = self._listener.getsockname()[1]
        except OSError as exc:
            _safe_close(self._listener)
            if exc.errno in (errno.EPERM, errno.EACCES):
                raise unittest.SkipTest('socket creation not permitted')
            raise
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        _safe_close(self._listener)
        _safe_close(self._client_sock)
        _safe_close(self._target_sock)
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def wait_for_request(self, timeout=1.0):
        return self._request_event.wait(timeout)

    def _run(self):
        try:
            client = self._accept_client()
            if client is None:
                return
            self._client_sock = client
            request = self._read_request(client)
            self.last_request = request
            self._request_event.set()
            if self._status_code is None:
                self._drain_until_close(client)
                return
            if self._status_code != 200:
                self._send_response(client, self._status_code)
                return
            target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target.connect((self._target_host, self._target_port))
            self._target_sock = target
            if self._response_delay:
                time.sleep(self._response_delay)
            self._send_response(client, 200)
            self._relay(client, target)
        finally:
            _safe_close(self._client_sock)
            _safe_close(self._target_sock)
            _safe_close(self._listener)

    def _accept_client(self):
        while not self._stop_event.is_set():
            try:
                client, _addr = self._listener.accept()
                return client
            except socket.timeout:
                continue
            except socket.error:
                return None
        return None

    def _read_request(self, client):
        buf = bytearray()
        client.settimeout(0.1)
        while not self._stop_event.is_set():
            if len(buf) > _PROXY_HEADER_LIMIT:
                break
            try:
                data = client.recv(4096)
            except socket.timeout:
                continue
            except socket.error:
                break
            if not data:
                break
            buf.extend(data)
            if b'\r\n\r\n' in buf:
                break
        return bytes(buf)

    def _send_response(self, client, status_code):
        if status_code == 200:
            reason = 'Connection established'
        else:
            reason = 'Proxy error'
        response = 'HTTP/1.1 %d %s\r\n\r\n' % (status_code, reason)
        client.sendall(response.encode('ascii'))

    def _relay(self, client, target):
        client.settimeout(0.1)
        target.settimeout(0.1)
        sockets = [client, target]
        while not self._stop_event.is_set():
            try:
                ready, _, _ = select.select(sockets, [], [], 0.1)
            except select.error:
                return
            for sock in ready:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                except socket.error:
                    return
                if not data:
                    return
                other = target if sock is client else client
                try:
                    other.sendall(data)
                except socket.error:
                    return

    def _drain_until_close(self, client):
        client.settimeout(0.1)
        while not self._stop_event.is_set():
            try:
                data = client.recv(4096)
            except socket.timeout:
                continue
            except socket.error:
                return
            if not data:
                return


class TlsClientServerTests(unittest.TestCase):
    def tearDown(self):
        time_provider.reset_time_source()

    def test_round_trip(self):
        port = _get_ephemeral_port()
        server_cfg = Config(
            transport='tls_handshake',
            tls_listen_addr='127.0.0.1:%d' % port,
        )
        server = TlsServer(server_cfg)
        client_cfg = Config(
            transport='tls_handshake',
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

    def test_round_trip_via_http_proxy(self):
        port = _get_ephemeral_port()
        server_cfg = Config(
            transport='tls_handshake',
            tls_listen_addr='127.0.0.1:%d' % port,
        )
        server = TlsServer(server_cfg)
        proxy = HttpConnectProxy('127.0.0.1', port)
        try:
            proxy.start()
        except unittest.SkipTest:
            server.close()
            raise
        client_cfg = Config(
            transport='tls_handshake',
            tls_target='127.0.0.1:%d' % port,
            tls_http_proxy='127.0.0.1:%d' % proxy.port,
            tls_http_proxy_auth='user:pass',
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
            self.assertTrue(proxy.wait_for_request(timeout=1.0))
            expected_auth = base64.b64encode(b'user:pass')
            self.assertIn(
                b'Proxy-Authorization: Basic ' + expected_auth,
                proxy.last_request,
            )
        finally:
            client.close()
            proxy.stop()
            server.close()

    def test_proxy_non_200_response(self):
        port = _get_ephemeral_port()
        proxy = HttpConnectProxy('127.0.0.1', port, status_code=403)
        proxy.start()
        client_cfg = Config(
            transport='tls_handshake',
            tls_target='127.0.0.1:%d' % port,
            tls_http_proxy='127.0.0.1:%d' % proxy.port,
        )
        client = TlsClient(client_cfg)
        try:
            permit = client.reserve_send()
            self.assertIsNotNone(permit)
            client.send(b'ping', permit)
            deadline = time.time() + 1.0
            while time.time() < deadline and client.pending_count():
                client.recv(timeout=0.05)
                time.sleep(0.01)
            self.assertEqual(client.pending_count(), 0)
        finally:
            client.close()
            proxy.stop()

    def test_proxy_timeout(self):
        port = _get_ephemeral_port()
        proxy = HttpConnectProxy('127.0.0.1', port, status_code=None)
        proxy.start()
        client_cfg = Config(
            transport='tls_handshake',
            tls_target='127.0.0.1:%d' % port,
            tls_http_proxy='127.0.0.1:%d' % proxy.port,
            tls_proxy_timeout=0.2,
        )
        client = TlsClient(client_cfg)
        try:
            permit = client.reserve_send()
            self.assertIsNotNone(permit)
            client.send(b'ping', permit)
            deadline = time.time() + 1.0
            while time.time() < deadline and client.pending_count():
                client.recv(timeout=0.05)
                time.sleep(0.01)
            self.assertEqual(client.pending_count(), 0)
        finally:
            client.close()
            proxy.stop()

    def test_pending_limit(self):
        port = _get_ephemeral_port()
        server = TlsServer(Config(transport='tls_handshake',
                                  tls_listen_addr='127.0.0.1:%d' % port,
                                  max_in_flight=1))
        client = TlsClient(Config(transport='tls_handshake',
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
            transport='tls_handshake',
            tls_pending_timeout=1.0,
            tls_connect_timeout=2.0,
            tls_handshake_timeout=2.0,
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')

    def test_invalid_sni_alpn(self):
        cfg = Config(
            transport='tls_handshake',
            tls_sni='bad..name',
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')
        cfg = Config(
            transport='tls_handshake',
            tls_alpn='h2,,http/1.1',
        )
        with self.assertRaises(TransportError):
            validate_tls_config(cfg, 'client')


if __name__ == '__main__':
    unittest.main()
