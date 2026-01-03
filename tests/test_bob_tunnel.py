# -*- coding: ascii -*-
"""Tests for BobTunnel."""

from __future__ import absolute_import

import threading
import unittest

from sfb.config import Config
from sfb.tunnel import BobTunnel, TunnelState
from sfb.transport import Server


def make_test_config(**overrides):
    """Create a Config for testing with sensible defaults."""
    defaults = {
        'dns_base_domain': 'test.local',
        'tunnel_idle_timeout': 60.0,
        'tunnel_keepalive_interval': 5.0,
        'max_in_flight': 16,
        'tunnel_connect_timeout': 10.0,
        'tunnel_timeout_packets': 100,
    }
    defaults.update(overrides)
    return Config(**defaults)


class MockServer(Server):
    """Mock transport server for testing Bob."""

    def __init__(self):
        self._requests = []
        self._responses = []
        self._pending = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._closed = False

    def recv(self, timeout=None):
        if self._closed:
            return None, None

        if self._event.wait(timeout):
            with self._lock:
                if self._pending:
                    data = self._pending
                    self._pending = None
                    self._event.clear()
                    return data, self._make_responder()
        return None, None

    def _make_responder(self):
        def responder(data):
            with self._lock:
                self._responses.append(data)
        return responder

    def send_request(self, data):
        """Inject a request (for testing)."""
        with self._lock:
            self._pending = data
            self._requests.append(data)
            self._event.set()

    def get_last_response(self):
        """Get the last response sent."""
        with self._lock:
            if self._responses:
                return self._responses[-1]
        return None

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._closed = True
        self._event.set()


class BobTunnelTests(unittest.TestCase):
    def test_initial_state(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)

    def test_close_sets_state(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel.close()
        self.assertEqual(tunnel.state, TunnelState.CLOSED)
        self.assertTrue(server._closed)
