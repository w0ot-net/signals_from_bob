# -*- coding: ascii -*-
"""Tests for tunnel module."""

from __future__ import absolute_import

import threading
import time
import unittest
import json

from sfb.config import Config
from sfb.tunnel import (
    AliceTunnel,
    BobTunnel,
    BaseTunnel,
    TunnelState,
    TunnelError,
)
from sfb.crypto import Plain, XOR
from sfb.transport import (
    Transport,
    Server,
)


def make_test_config(**overrides):
    """Create a Config for testing with sensible defaults."""
    defaults = {
        'dns_base_domain': 'test.local',
        'tunnel_idle_timeout': 60.0,
        'tunnel_keepalive_interval': 5.0,
        'tunnel_max_in_flight': 16,
        'tunnel_connect_timeout': 10.0,
        'tunnel_timeout_packets': 100,
    }
    defaults.update(overrides)
    return Config(**defaults)


class MockTransport(Transport):
    """Mock transport for testing Alice with pipelined send/recv."""

    def __init__(self, responses=None, max_pending=16):
        self._responses = list(responses) if responses else []
        self._pending = []  # List of (corr_id, response)
        self._next_corr_id = 0
        self._sent = []
        self._closed = False
        self._max_pending = max_pending

    def send(self, data):
        self._sent.append(data)
        corr_id = self._next_corr_id
        self._next_corr_id += 1

        # Queue response if available
        if self._responses:
            response = self._responses.pop(0)
            self._pending.append((corr_id, response))

        return corr_id

    def recv(self, timeout=None):
        if self._pending:
            return self._pending.pop(0)
        return (None, None)

    def pending_count(self):
        return len(self._pending)

    def can_send(self):
        return self.pending_count() < self._max_pending

    @property
    def max_pending(self):
        return self._max_pending

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._closed = True


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


def _control_messages(control):
    data = b''.join(list(control._send_buf))
    lines = [line for line in data.split(b'\n') if line]
    msgs = []
    for line in lines:
        msgs.append(json.loads(line.decode('ascii')))
    return msgs


class PairedTransport(object):
    """
    Paired transports for end-to-end testing.

    Creates a linked Alice transport and Bob server that communicate
    through shared queues.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._alice_to_bob = []
        self._bob_to_alice = []  # List of (corr_id, data)
        self._alice_pending = []  # corr_id's awaiting response
        self._next_corr_id = 0
        self._alice_event = threading.Event()
        self._bob_event = threading.Event()
        self._closed = False

    def make_alice_transport(self):
        return _PairedAliceTransport(self)

    def make_bob_server(self):
        return _PairedBobServer(self)


class WindowGrowthTest(unittest.TestCase):
    def test_window_growth_request(self):
        config = make_test_config(
            tunnel_window_growth_enabled=True,
            tunnel_window_growth_interval=0.01,
            tunnel_window_growth_mode='linear',
            tunnel_window_growth_step=1,
            tunnel_max_in_flight=4,
        )
        transport = MockTransport()
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._window_negotiated = True
        alice._negotiated_window = 1
        alice._send_window._max_in_flight = 1
        alice._proposed_max_in_flight = 4
        alice._ack_progressed = True

        alice._maybe_request_window(time.time())

        msgs = _control_messages(alice.control)
        window_msgs = [m for m in msgs if m.get('t') == 'tun' and m.get('c') == 'window']
        self.assertEqual(len(window_msgs), 1)
        self.assertEqual(window_msgs[0].get('size'), 2)


class _PairedAliceTransport(Transport):
    def __init__(self, pair):
        self._pair = pair

    def send(self, data):
        with self._pair._lock:
            corr_id = self._pair._next_corr_id
            self._pair._next_corr_id += 1
            self._pair._alice_to_bob.append((corr_id, data))
            self._pair._alice_pending.append(corr_id)
            self._pair._bob_event.set()
        return corr_id

    def recv(self, timeout=None):
        if timeout == 0:
            # Non-blocking poll
            with self._pair._lock:
                if self._pair._bob_to_alice:
                    corr_id, data = self._pair._bob_to_alice.pop(0)
                    if corr_id in self._pair._alice_pending:
                        self._pair._alice_pending.remove(corr_id)
                    return corr_id, data
            return None, None

        # Blocking wait
        deadline = time.time() + (timeout if timeout else 3600)
        while time.time() < deadline:
            with self._pair._lock:
                if self._pair._bob_to_alice:
                    corr_id, data = self._pair._bob_to_alice.pop(0)
                    if corr_id in self._pair._alice_pending:
                        self._pair._alice_pending.remove(corr_id)
                    return corr_id, data
            if self._pair._alice_event.wait(timeout=0.01):
                self._pair._alice_event.clear()
        return None, None

    def pending_count(self):
        with self._pair._lock:
            return len(self._pair._alice_pending)

    def can_send(self):
        return self.pending_count() < self.max_pending

    @property
    def max_pending(self):
        return 16

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._pair._closed = True
        self._pair._alice_event.set()
        self._pair._bob_event.set()


class _PairedBobServer(Server):
    def __init__(self, pair):
        self._pair = pair
        self._current_corr_id = None

    def recv(self, timeout=None):
        if self._pair._closed:
            return None, None

        deadline = time.time() + (timeout if timeout else 3600)
        while time.time() < deadline:
            with self._pair._lock:
                if self._pair._alice_to_bob:
                    corr_id, data = self._pair._alice_to_bob.pop(0)
                    self._current_corr_id = corr_id
                    return data, self._make_responder(corr_id)
            if self._pair._bob_event.wait(timeout=0.01):
                self._pair._bob_event.clear()
        return None, None

    def _make_responder(self, corr_id):
        def responder(data):
            with self._pair._lock:
                self._pair._bob_to_alice.append((corr_id, data))
                self._pair._alice_event.set()
        return responder

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._pair._closed = True
        self._pair._alice_event.set()
        self._pair._bob_event.set()


class AliceRateLimitTest(unittest.TestCase):
    def test_can_send_new_respects_limiter(self):
        config = make_test_config(
            tunnel_send_rate=1.0,
            tunnel_send_burst=1.0,
        )
        transport = MockTransport()
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._state = TunnelState.CONNECTED

        limiter = alice._send_limiter
        limiter._bucket._tokens = 0.0
        limiter._bucket._last_refill = time.time()

        self.assertFalse(alice._can_send_new())

        limiter._bucket._tokens = 0.0
        limiter._bucket._last_refill = time.time() - 2.0
        self.assertTrue(alice._can_send_new())


class BaseTunnelTests(unittest.TestCase):
    def test_initial_state_disconnected(self):
        tunnel = BaseTunnel(make_test_config())
        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)
        self.assertFalse(tunnel.connected)

    def test_has_channel_manager(self):
        tunnel = BaseTunnel(make_test_config())
        self.assertIsNotNone(tunnel.channel_manager)
        self.assertIsNotNone(tunnel.control)

    def test_encrypt_decrypt_roundtrip(self):
        tunnel = BaseTunnel(make_test_config(), crypto=XOR(b'secret'))
        data = b'hello world'
        encrypted = tunnel._encrypt(data)
        decrypted = tunnel._decrypt(encrypted)
        self.assertEqual(decrypted, data)
        self.assertNotEqual(encrypted, data)

    def test_plain_crypto_passthrough(self):
        tunnel = BaseTunnel(make_test_config(), crypto=Plain())
        data = b'hello world'
        encrypted = tunnel._encrypt(data)
        self.assertEqual(encrypted, data)


class AliceTunnelTests(unittest.TestCase):
    def test_requires_connected_for_tick(self):
        transport = MockTransport()
        tunnel = AliceTunnel(transport, make_test_config())
        self.assertFalse(tunnel.tick())

    def test_handshake_timeout(self):
        transport = MockTransport(responses=[])
        tunnel = AliceTunnel(transport, make_test_config())
        with self.assertRaises(TunnelError):
            tunnel.connect(timeout=0.1)
        self.assertEqual(tunnel.state, TunnelState.DISCONNECTED)

    def test_close_sets_state(self):
        transport = MockTransport()
        tunnel = AliceTunnel(transport, make_test_config())
        tunnel.close()
        self.assertEqual(tunnel.state, TunnelState.CLOSED)
        self.assertTrue(transport._closed)


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


class EndToEndTests(unittest.TestCase):
    def test_handshake(self):
        """Test Alice and Bob can complete handshake."""
        pair = PairedTransport()
        alice_transport = pair.make_alice_transport()
        bob_server = pair.make_bob_server()
        config = make_test_config()

        alice = AliceTunnel(alice_transport, config, crypto=Plain())
        bob = BobTunnel(bob_server, config, crypto=Plain())

        # Run Bob in background
        bob_thread = threading.Thread(target=bob.serve_forever)
        bob_thread.daemon = True
        bob_thread.start()

        # Alice connects
        try:
            alice.connect(timeout=2.0)
            self.assertEqual(alice.state, TunnelState.CONNECTED)

            # Give Bob time to process
            time.sleep(0.1)
            self.assertEqual(bob.state, TunnelState.CONNECTED)

        finally:
            alice.close()
            bob.close()
            bob_thread.join(timeout=1.0)

    def test_data_exchange(self):
        """Test Alice and Bob can exchange data via tick()."""
        pair = PairedTransport()
        alice_transport = pair.make_alice_transport()
        bob_server = pair.make_bob_server()
        config = make_test_config()

        alice = AliceTunnel(alice_transport, config, crypto=Plain())
        bob = BobTunnel(bob_server, config, crypto=Plain())

        # Run Bob in background
        bob_thread = threading.Thread(target=bob.serve_forever)
        bob_thread.daemon = True
        bob_thread.start()

        try:
            alice.connect(timeout=2.0)
            self.assertEqual(alice.state, TunnelState.CONNECTED)

            # Tick a few times - ping/pong are handled internally
            initial_sent = alice._packets_sent
            for _ in range(3):
                alice.tick()
                time.sleep(0.05)

            # Verify packets were exchanged
            self.assertGreater(alice._packets_sent, initial_sent)
            self.assertGreater(alice._packets_received, 0)

        finally:
            alice.close()
            bob.close()
            bob_thread.join(timeout=1.0)


class RecvWindowIntegrationTests(unittest.TestCase):
    """Tests for receive window integration."""

    def test_ack_advances_with_received_packets(self):
        """Verify recv_window.ack advances as packets are received."""
        from sfb.protocol import Packet, Segment

        pair = PairedTransport()
        alice_transport = pair.make_alice_transport()
        bob_server = pair.make_bob_server()
        config = make_test_config()

        alice = AliceTunnel(alice_transport, config, crypto=Plain())
        bob = BobTunnel(bob_server, config, crypto=Plain())

        bob_thread = threading.Thread(target=bob.serve_forever)
        bob_thread.daemon = True
        bob_thread.start()

        try:
            alice.connect(timeout=2.0)

            # After handshake, recv_window should be initialized
            initial_ack = alice._recv_window.ack
            self.assertIsNotNone(initial_ack)

            # Tick to receive packets from Bob
            for _ in range(3):
                alice.tick()
                time.sleep(0.05)

            # ack should have advanced
            self.assertNotEqual(alice._recv_window.ack, 0)

        finally:
            alice.close()
            bob.close()
            bob_thread.join(timeout=1.0)

    def test_duplicate_packets_ignored(self):
        """Verify duplicate packets are filtered by recv_window."""
        from sfb.tunnel.base_tunnel import BaseTunnel
        from sfb.protocol import Packet, Segment

        tunnel = BaseTunnel(make_test_config())
        tunnel._recv_window.set_initial_seq(1)

        # Create a packet with seq=1
        pkt = Packet(seq=1, ack=0, sack=0, flags=0)
        pkt.add_segment(Segment(channel=0, data=b'test'))

        # First delivery should work
        tunnel._process_incoming_packet(pkt)
        first_recv_size = tunnel.control._recv_buf_size

        # Second delivery of same seq should be filtered
        tunnel._process_incoming_packet(pkt)
        self.assertEqual(tunnel.control._recv_buf_size, first_recv_size)


class BobRetransmitTests(unittest.TestCase):
    """Tests for Bob's opportunistic retransmission."""

    def test_retransmit_rebuilds_with_fresh_ack(self):
        """Verify Bob rebuilds retransmits with fresh ack/sack."""
        server = MockServer()
        bob = BobTunnel(server, make_test_config(), crypto=Plain())

        # Simulate connected state
        bob._set_state(TunnelState.CONNECTED)
        bob._local_isn = 100
        bob._remote_isn = 200
        bob._recv_window.set_initial_seq(201)
        bob._send_window._next_seq = 101

        # Queue some data and "send" it (simulating a previous response)
        bob.control.send_message({'t': 'tun', 'c': 'test'})

        # Manually trigger a send to record in send_window
        from sfb.protocol import Packet, Segment, PACKET_HEADER_SIZE
        segments = bob._collect_segments(200 - PACKET_HEADER_SIZE)
        packet, seq = bob._build_packet(segments=segments)
        bob._send_window.send(segments)

        # Now verify there's an unacked packet with segments
        oldest = bob._send_window.get_oldest_unacked()
        self.assertIsNotNone(oldest)
        stored_seq, stored_segments = oldest
        self.assertEqual(stored_seq, 101)
        self.assertEqual(stored_segments, segments)

        # Simulate receiving a packet (to change ack state)
        bob._recv_window.set_initial_seq(202)  # Simulate ack advancement

        # Create a mock responder to capture what Bob sends
        sent_responses = []
        def mock_responder(data):
            sent_responses.append(data)

        # Call _send_response - should retransmit with fresh ack/sack
        bob._send_response(mock_responder, time.time())

        # Verify a packet was sent
        self.assertEqual(len(sent_responses), 1)

        # Decode and verify the retransmit has fresh ack
        response_packet = Packet.decode(sent_responses[0])
        self.assertEqual(response_packet.seq, 101)  # Same seq as original
        self.assertEqual(response_packet.ack, 202)  # Fresh ack value


class WindowEnforcementTests(unittest.TestCase):
    """Tests for send window enforcement."""

    def test_alice_respects_window_limit(self):
        """Verify Alice doesn't exceed max_in_flight."""
        from sfb.protocol import Segment
        transport = MockTransport()
        alice = AliceTunnel(transport, make_test_config(tunnel_max_in_flight=2), crypto=Plain())

        # Simulate post-negotiation state (window starts at 1 until negotiated)
        alice._send_window._max_in_flight = 2
        alice._negotiated_window = 2

        # Fill the window manually with segment lists
        alice._send_window.send([Segment(0, b'pkt1')])
        alice._send_window.send([Segment(0, b'pkt2')])

        self.assertFalse(alice._send_window.can_send)

    def test_bob_respects_window_limit(self):
        """Verify Bob doesn't exceed max_in_flight."""
        from sfb.protocol import Segment
        server = MockServer()
        bob = BobTunnel(server, make_test_config(tunnel_max_in_flight=2), crypto=Plain())

        # Simulate post-negotiation state (window starts at 1 until negotiated)
        bob._send_window._max_in_flight = 2
        bob._negotiated_window = 2

        # Simulate connected state
        bob._set_state(TunnelState.CONNECTED)
        bob._local_isn = 100
        bob._recv_window.set_initial_seq(1)
        bob._send_window._next_seq = 101

        # Fill the window with segment lists
        bob._send_window.send([Segment(0, b'pkt1')])
        bob._send_window.send([Segment(0, b'pkt2')])

        self.assertFalse(bob._send_window.can_send)

        # Bob should retransmit oldest when window is full (carries fresh ack)
        sent_responses = []
        def mock_responder(data):
            sent_responses.append(data)

        initial_unacked = bob._send_window.unacked_count
        bob._send_response(mock_responder, time.time())

        # Should have retransmitted (not added new packet)
        # and should have sent something
        self.assertEqual(len(sent_responses), 1)
        self.assertEqual(bob._send_window.unacked_count, initial_unacked)


class IdleTimeoutTests(unittest.TestCase):
    """Tests for idle timeout behavior."""

    def test_connecting_state_times_out(self):
        """Verify Bob times out stalled handshakes."""
        server = MockServer()
        bob = BobTunnel(server, make_test_config(tunnel_idle_timeout=0.1), crypto=Plain())

        # Simulate a stalled handshake
        bob._set_state(TunnelState.CONNECTING)
        bob._last_request_time = time.time() - 0.2  # 200ms ago

        # Should detect timeout
        result = bob._check_idle_timeout()
        self.assertTrue(result)
        self.assertEqual(bob.state, TunnelState.CLOSED)

    def test_disconnected_state_no_timeout(self):
        """Verify DISCONNECTED state doesn't trigger timeout."""
        server = MockServer()
        bob = BobTunnel(server, make_test_config(tunnel_idle_timeout=0.1), crypto=Plain())

        bob._last_request_time = time.time() - 1.0  # Long ago

        result = bob._check_idle_timeout()
        self.assertFalse(result)
        self.assertEqual(bob.state, TunnelState.DISCONNECTED)


class ControlMessageTests(unittest.TestCase):
    """Tests for control message handling."""

    def test_ping_triggers_pong(self):
        """Verify ping message causes pong to be queued for sending."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        # Initially no data queued
        self.assertEqual(tunnel.control.send_buf_size, 0)

        # Handle a ping message (new format)
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'ping'})

        # Pong should be queued in control channel's send buffer
        self.assertGreater(tunnel.control.send_buf_size, 0)
        # Check the actual content
        send_data = b''.join(tunnel.control._send_buf)
        self.assertIn(b'"t":"tun"', send_data)
        self.assertIn(b'"c":"pong"', send_data)

    def test_missing_t_field_dropped(self):
        """Verify messages without t field are dropped."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        # Message without t field should be dropped
        tunnel._dispatch_control_message({'c': 'ping'})

        # No pong should be queued (message was invalid)
        self.assertEqual(tunnel.control.send_buf_size, 0)

    def test_unknown_messages_logged_not_error(self):
        """Verify unknown control messages don't raise errors."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        # Should not raise
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'unknown_cmd'})
        tunnel._dispatch_control_message({'t': 'unknown_type', 'c': 'foo'})
        tunnel._dispatch_control_message({'foo': 'bar'})


class NegotiationTests(unittest.TestCase):
    """Tests for MTU/window negotiation."""

    def test_mtu_negotiation_bob_responds(self):
        """Verify Bob responds to MTU request with mtu_ok."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config(), is_initiator=False)
        tunnel._proposed_recv_mtu = 200  # Bob receive max
        tunnel._proposed_send_mtu = 180  # Bob send max

        # Alice requests tx=500, rx=150
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'mtu', 'tx': 500, 'rx': 150})

        # Bob should agree to rx=min(500, 200) = 200 for receiving
        self.assertEqual(tunnel._negotiated_recv_mtu, 200)
        # But _mtu_negotiated is False until Bob receives mtu_ack
        self.assertFalse(tunnel._mtu_negotiated)
        # And _send_mtu stays at default until ack
        self.assertEqual(tunnel._send_mtu, 100)

        # Check mtu_ok was queued
        send_data = b''.join(tunnel.control._send_buf)
        self.assertIn(b'"c":"mtu_ok"', send_data)
        self.assertIn(b'"tx":150', send_data)
        self.assertIn(b'"rx":200', send_data)

        # After receiving mtu_ack, Bob can send larger packets
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'mtu_ack'})
        self.assertTrue(tunnel._mtu_negotiated)
        self.assertEqual(tunnel._send_mtu, 150)

    def test_mtu_negotiation_alice_accepts(self):
        """Verify Alice accepts mtu_ok, updates MTU, and sends mtu_ack."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config(), is_initiator=True)
        tunnel._proposed_send_mtu = 200  # Alice send max
        tunnel._proposed_recv_mtu = 160  # Alice recv max

        # Default is 100
        self.assertEqual(tunnel._negotiated_recv_mtu, 100)
        self.assertEqual(tunnel._negotiated_send_mtu, 100)
        self.assertEqual(tunnel._send_mtu, 100)

        # Bob sends mtu_ok
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'mtu_ok', 'tx': 150, 'rx': 140})

        # Alice updates both receive and send MTU immediately
        self.assertEqual(tunnel._negotiated_recv_mtu, 150)
        self.assertEqual(tunnel._negotiated_send_mtu, 140)
        self.assertEqual(tunnel._send_mtu, 140)
        self.assertTrue(tunnel._mtu_negotiated)

        # Check mtu_ack was queued
        send_data = b''.join(tunnel.control._send_buf)
        self.assertIn(b'"c":"mtu_ack"', send_data)

    def test_window_negotiation_bob_responds(self):
        """Verify Bob responds to window request with window_ok."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config(tunnel_max_in_flight=8), is_initiator=False)

        # Alice requests window of 16
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'window', 'size': 16})

        # Bob should agree to min(16, 8, 16) = 8
        self.assertEqual(tunnel._negotiated_window, 8)
        self.assertTrue(tunnel._window_negotiated)
        self.assertEqual(tunnel._send_window._max_in_flight, 8)

        # Check window_ok was queued
        send_data = b''.join(tunnel.control._send_buf)
        self.assertIn(b'"c":"window_ok"', send_data)
        self.assertIn(b'"size":8', send_data)

    def test_window_negotiation_alice_accepts(self):
        """Verify Alice accepts window_ok and updates window limit."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config(), is_initiator=True)

        # Default is 1
        self.assertEqual(tunnel._negotiated_window, 1)
        self.assertEqual(tunnel._send_window._max_in_flight, 1)

        # Bob sends window_ok
        tunnel._dispatch_control_message({'t': 'tun', 'c': 'window_ok', 'size': 12})

        self.assertEqual(tunnel._negotiated_window, 12)
        self.assertTrue(tunnel._window_negotiated)
        self.assertEqual(tunnel._send_window._max_in_flight, 12)

    def test_initial_state_conservative(self):
        """Verify tunnel starts with conservative MTU/window."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        # Pre-negotiation defaults
        self.assertEqual(tunnel._negotiated_send_mtu, 100)
        self.assertEqual(tunnel._negotiated_recv_mtu, 100)
        self.assertEqual(tunnel._negotiated_window, 1)
        self.assertEqual(tunnel._send_window._max_in_flight, 1)
        self.assertFalse(tunnel._mtu_negotiated)
        self.assertFalse(tunnel._window_negotiated)


class ModuleRegistrationTests(unittest.TestCase):
    """Tests for module registration and dispatch."""

    def test_register_module(self):
        """Verify modules can be registered."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())
        received = []

        def handler(msg):
            received.append(msg)

        tunnel.register_module('test', handler)

        # Dispatch a message to the module
        tunnel._dispatch_control_message({'t': 'test', 'c': 'foo', 'data': 123})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['c'], 'foo')
        self.assertEqual(received[0]['data'], 123)

    def test_cannot_register_reserved_type(self):
        """Verify reserved types cannot be overridden."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        with self.assertRaises(ValueError):
            tunnel.register_module('tun', lambda msg: None)

        with self.assertRaises(ValueError):
            tunnel.register_module('ch', lambda msg: None)

    def test_cannot_register_duplicate(self):
        """Verify duplicate registration raises error."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())
        tunnel.register_module('mymod', lambda msg: None)

        with self.assertRaises(ValueError):
            tunnel.register_module('mymod', lambda msg: None)

    def test_unregister_module(self):
        """Verify modules can be unregistered."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())
        received = []

        tunnel.register_module('test', lambda msg: received.append(msg))
        tunnel._dispatch_control_message({'t': 'test', 'c': 'foo'})
        self.assertEqual(len(received), 1)

        # Unregister
        result = tunnel.unregister_module('test')
        self.assertTrue(result)

        # Message should now be dropped
        tunnel._dispatch_control_message({'t': 'test', 'c': 'bar'})
        self.assertEqual(len(received), 1)  # No new messages

    def test_unregister_nonexistent(self):
        """Verify unregistering nonexistent module returns False."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())
        result = tunnel.unregister_module('nonexistent')
        self.assertFalse(result)

    def test_module_error_handling(self):
        """Verify module errors are caught and logged."""
        from sfb.tunnel.base_tunnel import BaseTunnel

        tunnel = BaseTunnel(make_test_config())

        def bad_handler(msg):
            raise RuntimeError('Module error')

        tunnel.register_module('bad', bad_handler)

        # Should not raise - error is logged
        tunnel._dispatch_control_message({'t': 'bad', 'c': 'foo'})


class MessageFactoryTests(unittest.TestCase):
    """Tests for message factory functions."""

    def test_tun_ping(self):
        """Verify tun_ping creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_ping
        msg = tun_ping().to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'ping')

    def test_tun_pong(self):
        """Verify tun_pong creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_pong
        msg = tun_pong().to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'pong')

    def test_tun_mtu(self):
        """Verify tun_mtu creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_mtu
        msg = tun_mtu(500, 300).to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'mtu')
        self.assertEqual(msg['tx'], 500)
        self.assertEqual(msg['rx'], 300)

    def test_tun_mtu_ok(self):
        """Verify tun_mtu_ok creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_mtu_ok
        msg = tun_mtu_ok(512, 256).to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'mtu_ok')
        self.assertEqual(msg['tx'], 512)
        self.assertEqual(msg['rx'], 256)

    def test_tun_window(self):
        """Verify tun_window creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_window
        msg = tun_window(16).to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'window')
        self.assertEqual(msg['size'], 16)

    def test_tun_window_ok(self):
        """Verify tun_window_ok creates correct message."""
        from sfb.tunnel.tunnel_control_messages import tun_window_ok
        msg = tun_window_ok(8).to_dict()
        self.assertEqual(msg['t'], 'tun')
        self.assertEqual(msg['c'], 'window_ok')
        self.assertEqual(msg['size'], 8)

    def test_ch_open(self):
        """Verify ch_open creates correct message."""
        from sfb.tunnel.tunnel_control_messages import ch_open
        msg = ch_open(1).to_dict()
        self.assertEqual(msg['t'], 'ch')
        self.assertEqual(msg['c'], 'open')
        self.assertEqual(msg['ch'], 1)
        # Channels are generic - no atype/addr/port

    def test_ch_open_ok(self):
        """Verify ch_open_ok creates correct message."""
        from sfb.tunnel.tunnel_control_messages import ch_open_ok
        msg = ch_open_ok(3).to_dict()
        self.assertEqual(msg['t'], 'ch')
        self.assertEqual(msg['c'], 'open_ok')
        self.assertEqual(msg['ch'], 3)

    def test_ch_open_fail(self):
        """Verify ch_open_fail creates correct message."""
        from sfb.tunnel.tunnel_control_messages import ch_open_fail
        msg = ch_open_fail(5, 'connection refused').to_dict()
        self.assertEqual(msg['t'], 'ch')
        self.assertEqual(msg['c'], 'open_fail')
        self.assertEqual(msg['ch'], 5)
        self.assertEqual(msg['reason'], 'connection refused')

    def test_ch_close(self):
        """Verify ch_close creates correct message."""
        from sfb.tunnel.tunnel_control_messages import ch_close
        msg = ch_close(7).to_dict()
        self.assertEqual(msg['t'], 'ch')
        self.assertEqual(msg['c'], 'close')
        self.assertEqual(msg['ch'], 7)

    def test_ch_close_ok(self):
        """Verify ch_close_ok creates correct message."""
        from sfb.tunnel.tunnel_control_messages import ch_close_ok
        msg = ch_close_ok(9).to_dict()
        self.assertEqual(msg['t'], 'ch')
        self.assertEqual(msg['c'], 'close_ok')
        self.assertEqual(msg['ch'], 9)

    def test_encode(self):
        """Verify encode produces valid JSON bytes."""
        from sfb.tunnel.tunnel_control_messages import encode, tun_ping
        msg = tun_ping()
        encoded = encode(msg)
        self.assertIsInstance(encoded, bytes)
        self.assertTrue(encoded.endswith(b'\n'))
        # Verify it's valid JSON
        import json
        decoded = json.loads(encoded.decode('ascii'))
        self.assertEqual(decoded, msg.to_dict())

    def test_encode_compact_format(self):
        """Verify encode uses compact JSON format."""
        from sfb.tunnel.tunnel_control_messages import encode, tun_mtu
        msg = tun_mtu(512, 256)
        encoded = encode(msg)
        # Should have no spaces after separators
        self.assertNotIn(b': ', encoded)
        self.assertNotIn(b', ', encoded)


class ModuleLoaderTest(unittest.TestCase):
    def test_alice_enables_module_loader(self):
        config = make_test_config()
        transport = MockTransport()
        alice = AliceTunnel(transport, config, crypto=Plain())
        self.assertIsNotNone(alice.module_loader)

    def test_bob_enable_module_loader_allows_mod(self):
        config = make_test_config()
        server = MockServer()
        bob = BobTunnel(server, config, crypto=Plain())
        self.assertIsNone(bob.module_loader)
        self.assertNotIn('mod', bob._allowed_message_types)
        bob.enable_module_loader()
        self.assertIsNotNone(bob.module_loader)
        self.assertIn('mod', bob._allowed_message_types)


if __name__ == '__main__':
    unittest.main()
