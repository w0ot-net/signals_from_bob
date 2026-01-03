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
    Segment,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_SYN,
    PACKET_HEADER_SIZE,
    SEGMENT_HEADER_SIZE,
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


class ImmediateTimeoutServer(Server):
    """Server that always returns a timeout immediately."""

    def __init__(self):
        self._closed = False

    def recv(self, timeout=None):
        return None, None

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._closed = True


class OneShotServer(Server):
    """Server that returns one request then times out."""

    def __init__(self):
        self._closed = False
        self._request = None
        self._responder = None
        self._served = False
        self.tunnel = None

    def set_request(self, data, responder=None):
        self._request = data
        self._responder = responder

    def recv(self, timeout=None):
        if self._closed:
            return None, None
        if self._served or self._request is None:
            return None, None
        self._served = True
        if self.tunnel is not None:
            self.tunnel._bg_stop = True
        if self._responder is None:
            return self._request, lambda data: None
        return self._request, self._responder

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._closed = True


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
    def test_data_packet_ignored_when_disconnected(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        responses = []

        def responder(data):
            responses.append(data)

        data_packet = Packet(
            seq=1,
            ack=0,
            sack=0,
            flags=0,
        )
        tunnel.handle_request(_encode_for_bob(tunnel, data_packet), responder)

        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)
        self.assertFalse(tunnel._handshake_complete)
        self.assertEqual(responses, [])

    def test_ack_ignored_when_disconnected(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        responses = []

        def responder(data):
            responses.append(data)

        ack = Packet(
            seq=1,
            ack=1,
            sack=0,
            flags=FLAG_ACK,
        )
        tunnel.handle_request(_encode_for_bob(tunnel, ack), responder)

        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)
        self.assertFalse(tunnel._handshake_complete)
        self.assertEqual(responses, [])

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

    def test_ack_mismatch_does_not_complete_handshake(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())

        syn = Packet(seq=10, ack=0, sack=0, flags=FLAG_SYN)
        tunnel.handle_request(_encode_for_bob(tunnel, syn), lambda data: None)

        responses = []

        def responder(data):
            responses.append(data)

        ack = Packet(
            seq=11,
            ack=0,
            sack=0,
            flags=FLAG_ACK,
        )
        tunnel.handle_request(_encode_for_bob(tunnel, ack), responder)

        self.assertEqual(tunnel.state, TunnelState.CONNECTING)
        self.assertFalse(tunnel._handshake_complete)
        self.assertEqual(responses, [])

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
    def test_poll_ewma_updates(self):
        config = make_test_config(tunnel_bob_poll_ewma_alpha=0.5)
        server = MockServer()
        tunnel = BobTunnel(server, config)

        tunnel._update_poll_ewma(1.0)
        self.assertIsNone(tunnel._poll_interval_ewma)

        tunnel._update_poll_ewma(2.0)
        self.assertEqual(tunnel._poll_interval_ewma, 1.0)

        tunnel._update_poll_ewma(4.0)
        self.assertAlmostEqual(tunnel._poll_interval_ewma, 1.5)

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

    def test_send_response_sends_keepalive_when_idle(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._send_window._next_seq = 1

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, time_provider.now())

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.flags & FLAG_KEEPALIVE, FLAG_KEEPALIVE)

        body = responses[0][PACKET_HEADER_SIZE:]
        segments = Segment.decode_all(body)
        self.assertEqual(segments, [])

    def test_send_response_ack_only_when_pending_data_no_space(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._send_window._next_seq = 1
        tunnel._send_mtu = SEGMENT_HEADER_SIZE

        tunnel.control.send_message({'t': 'tun', 'c': 'ping'})

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, time_provider.now())

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.flags & FLAG_KEEPALIVE, 0)

        body = responses[0][PACKET_HEADER_SIZE:]
        segments = Segment.decode_all(body)
        self.assertEqual(segments, [])

    def test_send_response_retransmits_oldest(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)

        segment = Segment(channel=0, data=b'x')
        seq = tunnel._send_window.send(
            [segment],
            flags=0,
            now=time_provider.now() - 10.0,
        )

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, time_provider.now())

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.seq, seq)

        info = tunnel._send_window.get_unacked_info(seq)
        self.assertEqual(info[-1], 1)

    def test_send_response_window_full_retransmits_oldest(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._send_window._max_in_flight = 1

        now = time_provider.now()
        segment = Segment(channel=0, data=b'x')
        seq = tunnel._send_window.send(
            [segment],
            flags=0,
            now=now,
        )

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, now)

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.seq, seq)

        info = tunnel._send_window.get_unacked_info(seq)
        self.assertEqual(info[-1], 1)

    def test_send_response_window_distance_retransmits_oldest(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._send_window._max_in_flight = 2

        now = time_provider.now()
        segment = Segment(channel=0, data=b'x')
        seq = tunnel._send_window.send(
            [segment],
            flags=0,
            now=now,
        )
        tunnel._send_window._next_seq = 5
        tunnel._last_cum_ack = 0

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, now)

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.seq, seq)

        info = tunnel._send_window.get_unacked_info(seq)
        self.assertEqual(info[-1], 1)

    def test_send_response_skips_retransmit_on_cooldown(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._poll_interval_ewma = 1.0

        segment = Segment(channel=0, data=b'x')
        seq = tunnel._send_window.send(
            [segment],
            flags=0,
            now=time_provider.now(),
        )
        next_seq = tunnel._send_window.next_seq

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, time_provider.now())

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertEqual(header.seq, next_seq)

        info = tunnel._send_window.get_unacked_info(seq)
        self.assertEqual(info[-1], 0)

    def test_send_response_skips_retransmit_on_ack_progress(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)
        tunnel._send_window._max_in_flight = 2

        now = time_provider.now()
        segment = Segment(channel=0, data=b'x')
        seq = tunnel._send_window.send(
            [segment],
            flags=0,
            now=now - 1.0,
        )
        tunnel._last_cum_ack = 0
        tunnel._last_cum_ack_time = now - 0.01

        responses = []

        def responder(data):
            responses.append(data)

        tunnel._send_response(responder, now)

        self.assertEqual(len(responses), 1)
        header = PacketHeader.decode(responses[0])
        self.assertNotEqual(header.seq, seq)

        info = tunnel._send_window.get_unacked_info(seq)
        self.assertEqual(info[-1], 0)

    def test_payload_cap_limits_response(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        tunnel._set_state(TunnelState.CONNECTED)
        tunnel._recv_window.set_initial_seq(1)

        tunnel.control.send_message({
            't': 'tun',
            'c': 'ping',
            'data': 'x' * 50,
        })

        responses = []

        class Responder(object):
            def __init__(self, payload_cap):
                self.payload_cap = payload_cap
                self.qname_wire_len = 0
                self.max_packet_size = payload_cap

            def __call__(self, data):
                responses.append(data)

        payload_cap = PACKET_HEADER_SIZE + 10
        tunnel._send_response(Responder(payload_cap), time_provider.now())

        self.assertEqual(len(responses), 1)
        response_data = responses[0]
        self.assertLessEqual(len(response_data), payload_cap)

        body = response_data[PACKET_HEADER_SIZE:]
        segments = Segment.decode_all(body)
        self.assertEqual(len(segments), 1)
        max_data = payload_cap - PACKET_HEADER_SIZE - SEGMENT_HEADER_SIZE
        self.assertLessEqual(len(segments[0].data), max_data)

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


class BobRequestHandlingTests(unittest.TestCase):
    def test_handle_request_ignores_invalid_packet(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        responses = []

        def responder(data):
            responses.append(data)

        tunnel.handle_request(b'\x00', responder)
        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)
        self.assertEqual(responses, [])

    def test_handle_request_unexpected_state(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        responses = []

        def responder(data):
            responses.append(data)

        tunnel._set_state(TunnelState.CLOSING)
        packet = Packet(seq=1, ack=0, sack=0, flags=0)
        tunnel.handle_request(_encode_for_bob(tunnel, packet), responder)
        self.assertEqual(tunnel.state, TunnelState.CLOSING)
        self.assertEqual(responses, [])


class BobIdleTimeoutTests(unittest.TestCase):
    def test_idle_timeout_skips_without_last_request(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config(tunnel_idle_timeout=0.1))
        tunnel._set_state(TunnelState.CONNECTED)

        result = tunnel._check_idle_timeout()
        self.assertFalse(result)
        self.assertEqual(tunnel.state, TunnelState.CONNECTED)


class BobLoopTests(unittest.TestCase):
    def test_serve_forever_times_out_idle(self):
        server = ImmediateTimeoutServer()
        config = make_test_config(tunnel_idle_timeout=0.1)
        tunnel = BobTunnel(server, config)

        tunnel._set_state(TunnelState.CONNECTING)
        tunnel._last_request_time = time_provider.now() - 1.0
        tunnel.serve_forever()

        self.assertEqual(tunnel.state, TunnelState.CLOSED)

    def test_run_loop_processes_request(self):
        server = OneShotServer()
        tunnel = BobTunnel(server, make_test_config())
        server.tunnel = tunnel

        syn = Packet(seq=5, ack=0, sack=0, flags=FLAG_SYN)
        server.set_request(_encode_for_bob(tunnel, syn))

        tunnel._run_loop()
        self.assertEqual(tunnel.state, TunnelState.CONNECTING)


class BobResponderTests(unittest.TestCase):
    def test_respond_raises_on_responder_error(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())

        def responder(data):
            raise RuntimeError('boom')

        with self.assertRaises(RuntimeError):
            tunnel._respond(responder, b'payload', 'test')


class BobModuleLoaderTests(unittest.TestCase):
    def test_enable_module_loader_allows_mod(self):
        server = MockServer()
        tunnel = BobTunnel(server, make_test_config())
        self.assertNotIn('mod', tunnel._allowed_message_types)

        loader = tunnel.enable_module_loader()
        self.assertIsNotNone(loader)
        self.assertIn('mod', tunnel._allowed_message_types)
