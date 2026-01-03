# -*- coding: ascii -*-
"""Tests for BobTunnel."""

from __future__ import absolute_import

import threading
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.protocol import (
    Packet,
    PacketHeader,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_SYN,
)
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


def _encode_for_bob(tunnel, packet):
    body = tunnel._encode_segments(packet.segments)
    encrypted_body = tunnel._encrypt(
        body,
        seq=packet.seq,
        direction=tunnel._direction_inbound(),
    )
    return packet.header.encode() + encrypted_body


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


class BobHandshakeTests(unittest.TestCase):
    def test_syn_sends_synack(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        responses = []

        def responder(data):
            responses.append(data)

        syn = Packet(seq=10, ack=0, sack=0, flags=FLAG_SYN)
        tunnel.handle_request(_encode_for_bob(tunnel, syn), responder)

        self.assertEqual(tunnel.state, TunnelState.CONNECTING)
        self.assertEqual(tunnel._remote_isn, 10)
        self.assertEqual(tunnel._recv_window.ack, 11)
        self.assertEqual(tunnel._send_window.next_seq, tunnel._local_isn)
        self.assertEqual(len(responses), 1)

        header = PacketHeader.decode(responses[0])
        self.assertTrue(header.flags & FLAG_SYN)
        self.assertTrue(header.flags & FLAG_ACK)
        self.assertEqual(header.ack, 11)
        self.assertEqual(header.seq, tunnel._local_isn)

    def test_ack_completes_handshake(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())

        syn = Packet(seq=10, ack=0, sack=0, flags=FLAG_SYN)
        tunnel.handle_request(_encode_for_bob(tunnel, syn), lambda data: None)

        responses = []

        def responder(data):
            responses.append(data)

        ack = Packet(
            seq=11,
            ack=(tunnel._local_isn + 1) & 0xFFFF,
            sack=0,
            flags=FLAG_ACK,
        )
        tunnel.handle_request(_encode_for_bob(tunnel, ack), responder)

        self.assertEqual(tunnel.state, TunnelState.CONNECTED)
        self.assertTrue(tunnel._handshake_complete)
        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.flags & FLAG_KEEPALIVE, FLAG_KEEPALIVE)

    def test_data_packet_completes_handshake(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())

        syn = Packet(seq=10, ack=0, sack=0, flags=FLAG_SYN)
        tunnel.handle_request(_encode_for_bob(tunnel, syn), lambda data: None)

        responses = []

        def responder(data):
            responses.append(data)

        data_packet = Packet(
            seq=11,
            ack=(tunnel._local_isn + 1) & 0xFFFF,
            sack=0,
            flags=0,
        )
        tunnel.handle_request(_encode_for_bob(tunnel, data_packet), responder)

        self.assertEqual(tunnel.state, TunnelState.CONNECTED)
        self.assertTrue(tunnel._handshake_complete)
        self.assertEqual(len(responses), 1)


class BobControlMessageTests(unittest.TestCase):
    def test_message_rejected_until_allowed(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        calls = []

        def handler(msg):
            calls.append(msg)

        tunnel.register_module('x', handler)
        tunnel._dispatch_control_message({'t': 'x', 'c': 'noop'})
        self.assertEqual(calls, [])

        tunnel.allow_message_type('x')
        tunnel._dispatch_control_message({'t': 'x', 'c': 'noop'})
        self.assertEqual(len(calls), 1)


class BobTimingTests(unittest.TestCase):
    def test_poll_ewma_clamps_negative_interval(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())

        tunnel._update_poll_ewma(10.0)
        self.assertEqual(tunnel._last_request_time, 10.0)
        self.assertIsNone(tunnel._poll_interval_ewma)

        tunnel._update_poll_ewma(9.0)
        self.assertEqual(tunnel._last_request_time, 9.0)
        self.assertEqual(tunnel._poll_interval_ewma, 0.0)

    def test_retransmit_cooldown_bounds(self):
        config = make_test_config(
            tunnel_bob_retransmit_min_interval=0.5,
            tunnel_bob_retransmit_max_interval=1.0,
            tunnel_bob_retransmit_poll_factor=4.0,
        )
        server = MockServer()
        tunnel = BobTunnel(server, config)

        tunnel._poll_interval_ewma = 0.2
        cooldown = tunnel._retransmit_cooldown()
        self.assertAlmostEqual(cooldown, 0.8)

        tunnel._poll_interval_ewma = 1.0
        cooldown = tunnel._retransmit_cooldown()
        self.assertAlmostEqual(cooldown, 1.0)


class BobResponseTests(unittest.TestCase):
    def test_keepalive_suppressed_when_pending_data(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._local_isn = 10
        tunnel._remote_isn = 20
        tunnel._recv_window.set_initial_seq(21)
        tunnel._send_window._next_seq = 11
        tunnel._send_mtu = 1

        tunnel.control.send_message({'t': 'tun', 'c': 'ping'})

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, time_provider.now())

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.flags & FLAG_KEEPALIVE, 0)

    def test_retransmit_cap_closes(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        responses = []

        def responder(data):
            responses.append(data)

        ok = tunnel._send_retransmit_response(
            responder,
            1,
            time_provider.now(),
            1,
            [],
            0,
            None,
        )

        self.assertFalse(ok)
        self.assertEqual(tunnel.state, TunnelState.CLOSED)
        self.assertEqual(responses, [])
        self.assertTrue(server._closed)
