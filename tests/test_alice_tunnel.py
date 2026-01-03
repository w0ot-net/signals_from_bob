# -*- coding: ascii -*-
from __future__ import absolute_import

import json
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.crypto import Plain
from sfb.protocol import (
    Packet,
    Segment,
    CHANNEL_CONTROL,
    FLAG_KEEPALIVE,
    FLAG_SYN,
    FLAG_ACK,
    PACKET_HEADER_SIZE,
)
from sfb.tunnel import AliceTunnel, TunnelState
from sfb.transport import Transport


def make_test_config(**overrides):
    defaults = {
        'dns_base_domain': 'test.local',
    }
    defaults.update(overrides)
    return Config(**defaults)


def _control_messages(control):
    data = b''.join(list(control._send_buf))
    lines = [line for line in data.split(b'\n') if line]
    msgs = []
    for line in lines:
        msgs.append(json.loads(line.decode('ascii')))
    return msgs


class _DummyTransport(Transport):
    def __init__(self, send_mtu=200, recv_mtu=200, max_in_flight=16):
        super(_DummyTransport, self).__init__()
        self._send_mtu = send_mtu
        self._recv_mtu = recv_mtu
        self._max_in_flight = max_in_flight
        self._pending = []

    def reserve_send(self, now=None):
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if pending_before + reserved >= self._max_in_flight:
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def _send_impl(self, data, permit):
        self._pending.append((0, data))
        return 0

    def recv(self, timeout=None):
        return None, None

    def pending_count(self):
        return len(self._pending)

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu


class _QueueTransport(Transport):
    def __init__(self, responses=None, send_mtu=200, recv_mtu=200,
                 max_in_flight=16, fail_after=None):
        super(_QueueTransport, self).__init__()
        self._responses = list(responses) if responses else []
        self._send_mtu = send_mtu
        self._recv_mtu = recv_mtu
        self._max_in_flight = max_in_flight
        self._fail_after = fail_after
        self._send_calls = 0
        self._pending = []
        self._sent = []
        self._next_corr_id = 0

    def reserve_send(self, now=None):
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if pending_before + reserved >= self._max_in_flight:
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def _send_impl(self, data, permit):
        self._send_calls += 1
        if self._fail_after is not None and self._send_calls > self._fail_after:
            raise RuntimeError('send failure')
        self._sent.append(data)
        corr_id = self._next_corr_id
        self._next_corr_id += 1
        if self._responses:
            response = self._responses.pop(0)
            self._pending.append((corr_id, response))
        return corr_id

    def recv(self, timeout=None):
        if self._pending:
            return self._pending.pop(0)
        return None, None

    def pending_count(self):
        return len(self._pending)

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu


class _HeadroomTransport(Transport):
    def __init__(self, pending, max_in_flight=16, send_mtu=200, recv_mtu=200):
        super(_HeadroomTransport, self).__init__()
        self._pending = pending
        self._max_in_flight = max_in_flight
        self._send_mtu = send_mtu
        self._recv_mtu = recv_mtu
        self.release_calls = 0

    def reserve_send(self, now=None):
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if pending_before + reserved >= self._max_in_flight:
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def _send_impl(self, data, permit):
        return 0

    def recv(self, timeout=None):
        return None, None

    def pending_count(self):
        return self._pending

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    def release_send(self, permit):
        self.release_calls += 1
        return Transport.release_send(self, permit)


class _PendingBeforeTransport(Transport):
    def __init__(self, pending_before, max_in_flight=16, send_mtu=200, recv_mtu=200):
        super(_PendingBeforeTransport, self).__init__()
        self._pending_before = pending_before
        self._max_in_flight = max_in_flight
        self._send_mtu = send_mtu
        self._recv_mtu = recv_mtu
        self.release_calls = 0

    def reserve_send(self, now=None):
        self._ensure_reserved()
        return self._reserve_permit(now=now, pending_before=self._pending_before)

    def _send_impl(self, data, permit):
        return 0

    def recv(self, timeout=None):
        return None, None

    def pending_count(self):
        raise AssertionError('pending_count should not be called')

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    def release_send(self, permit):
        self.release_calls += 1
        return Transport.release_send(self, permit)


class _ErrorPendingTransport(Transport):
    def __init__(self, max_in_flight=16, send_mtu=200, recv_mtu=200):
        super(_ErrorPendingTransport, self).__init__()
        self._max_in_flight = max_in_flight
        self._send_mtu = send_mtu
        self._recv_mtu = recv_mtu
        self.release_calls = 0

    def reserve_send(self, now=None):
        self._ensure_reserved()
        return self._reserve_permit(now=now)

    def _send_impl(self, data, permit):
        return 0

    def recv(self, timeout=None):
        return None, None

    def pending_count(self):
        raise RuntimeError('pending_count error')

    @property
    def max_in_flight(self):
        return self._max_in_flight

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    def release_send(self, permit):
        self.release_calls += 1
        return Transport.release_send(self, permit)


class _ResponseAlice(AliceTunnel):
    def __init__(self, transport, config, crypto=None, logger=None):
        super(_ResponseAlice, self).__init__(
            transport,
            config,
            crypto=crypto,
            logger=logger,
        )
        self.process_calls = 0

    def _process_incoming_packet(self, packet, now=None, packet_size=None):
        self.process_calls += 1
        return ([], 0, 0)


class PollDecisionTests(unittest.TestCase):
    def test_pong_grace_forces_poll(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_keepalive_interval=5.0,
            tunnel_pong_grace_polls=2,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._last_was_pong_only = True
        alice._pong_grace_remaining = 2

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=1.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (True, False, True))

    def test_pong_grace_exhausted_waits_for_keepalive(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_keepalive_interval=5.0,
            tunnel_pong_grace_polls=0,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._last_was_pong_only = True
        alice._pong_grace_remaining = 0
        alice._last_send_time = 0.0

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=1.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (False, True, False))

    def test_data_or_pending_acks_poll_immediately(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_keepalive_interval=5.0)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._got_data = True
        alice._has_pending_data_acks = False

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=1.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (True, False, False))

    def test_pending_data_acks_poll_immediately(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_keepalive_interval=5.0)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._got_data = False
        alice._has_pending_data_acks = True

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=1.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (True, False, False))

    def test_keepalive_waits_for_interval(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_keepalive_interval=5.0)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._last_send_time = 1.0

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=2.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (False, True, False))

    def test_keepalive_due_without_pong_only(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_keepalive_interval=5.0)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._last_send_time = 0.0

        should_poll, keepalive_due, consume_grace = alice._poll_decision(now=6.0)

        self.assertEqual((should_poll, keepalive_due, consume_grace), (True, True, False))


class TransportHeadroomTests(unittest.TestCase):
    def test_headroom_blocks_reservation(self):
        transport = _HeadroomTransport(pending=14, max_in_flight=16)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        permit = alice._reserve_transport_permit(now=0.0)

        self.assertIsNone(permit)
        self.assertEqual(transport.release_calls, 1)

    def test_headroom_allows_reservation(self):
        transport = _HeadroomTransport(pending=13, max_in_flight=16)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        permit = alice._reserve_transport_permit(now=0.0)

        self.assertIsNotNone(permit)
        self.assertEqual(transport.release_calls, 0)
        transport.release_send(permit)

    def test_headroom_uses_pending_before(self):
        transport = _PendingBeforeTransport(pending_before=14, max_in_flight=16)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        permit = alice._reserve_transport_permit(now=0.0)

        self.assertIsNone(permit)
        self.assertEqual(transport.release_calls, 1)

    def test_headroom_skips_on_pending_count_error(self):
        transport = _ErrorPendingTransport(max_in_flight=16)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        permit = alice._reserve_transport_permit(now=0.0)

        self.assertIsNotNone(permit)
        self.assertEqual(transport.release_calls, 0)
        transport.release_send(permit)

    def test_headroom_disabled_for_tiny_window(self):
        transport = _HeadroomTransport(pending=0, max_in_flight=1)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        permit = alice._reserve_transport_permit(now=0.0)

        self.assertIsNotNone(permit)
        self.assertEqual(transport.release_calls, 0)
        transport.release_send(permit)


class ConnectTests(unittest.TestCase):
    def test_connect_queues_mtu_and_window(self):
        config = make_test_config(max_in_flight=8)
        syn_ack = Packet(seq=100, ack=2, sack=0, flags=FLAG_SYN | FLAG_ACK)
        ack_response = Packet(seq=101, ack=0, sack=0, flags=0)
        transport = _QueueTransport(
            responses=[syn_ack.encode(), ack_response.encode()],
            send_mtu=240,
            recv_mtu=180,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())

        alice.connect(timeout=1.0)

        self.assertEqual(alice.state, TunnelState.CONNECTED)
        msgs = _control_messages(alice.control)
        mtu_msgs = [
            m for m in msgs
            if m.get('t') == 'tun' and m.get('c') == 'mtu'
        ]
        window_msgs = [
            m for m in msgs
            if m.get('t') == 'tun' and m.get('c') == 'window'
        ]
        self.assertEqual(len(mtu_msgs), 1)
        self.assertEqual(len(window_msgs), 1)
        mtu_msg = mtu_msgs[0]
        self.assertEqual(
            mtu_msg.get('tx'),
            transport.send_mtu - PACKET_HEADER_SIZE,
        )
        self.assertEqual(
            mtu_msg.get('rx'),
            transport.recv_mtu - PACKET_HEADER_SIZE,
        )
        self.assertEqual(window_msgs[0].get('size'), 8)

    def test_connect_still_negotiates_on_ack_send_failure(self):
        config = make_test_config(max_in_flight=4)
        syn_ack = Packet(seq=10, ack=2, sack=0, flags=FLAG_SYN | FLAG_ACK)
        transport = _QueueTransport(
            responses=[syn_ack.encode()],
            fail_after=1,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())

        alice.connect(timeout=1.0)

        self.assertEqual(alice.state, TunnelState.CONNECTED)
        msgs = _control_messages(alice.control)
        self.assertTrue(
            any(m.get('t') == 'tun' and m.get('c') == 'mtu' for m in msgs)
        )
        self.assertTrue(
            any(m.get('t') == 'tun' and m.get('c') == 'window' for m in msgs)
        )


class SendGateTests(unittest.TestCase):
    def test_send_window_distance_blocks_new_send(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._last_cum_ack = 0
        alice._send_window._next_seq = alice.MAX_WINDOW + 1

        allowed = alice._can_send_new(now=0.0)

        self.assertFalse(allowed)

    def test_send_window_distance_respects_effective_cap(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_initial_window=10,
            tunnel_adaptive_pacing_enabled=True,
            tunnel_pace_target_inflight_ratio=0.25,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._last_cum_ack = 0
        alice._send_window._next_seq = 3

        allowed = alice._can_send_new(now=0.0)

        self.assertFalse(allowed)

    def test_send_window_full_blocks_new_send(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_initial_window=1)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._send_window.send([Segment(1, b'data')], now=0.0)

        allowed = alice._can_send_new(now=0.0)

        self.assertFalse(allowed)


class RetransmitTests(unittest.TestCase):
    def test_send_retransmit_returns_false_when_transport_blocked(self):
        transport = _DummyTransport(max_in_flight=0)
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())

        sent = alice._send_retransmit(
            0,
            [Segment(1, b'data')],
            0,
            None,
            now=0.0,
        )

        self.assertFalse(sent)

    def test_tick_retransmit_backoff_once(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_initial_window=3,
            tunnel_keepalive_interval=100.0,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._set_state(TunnelState.CONNECTED)

        now = time_provider.now()
        send_time = now - (alice._rtt.rto_sec + 0.5)
        alice._send_window.send([Segment(1, b'a')], now=send_time)
        alice._send_window.send([Segment(1, b'b')], now=send_time)
        alice._last_send_time = now

        before_rto = alice._rtt.rto_ms
        alice.tick()
        after_rto = alice._rtt.rto_ms

        self.assertEqual(after_rto, before_rto * 2)


class SendPacketTests(unittest.TestCase):
    def test_send_new_packet_rate_limit_releases_permit(self):
        transport = _HeadroomTransport(pending=0, max_in_flight=16)
        config = make_test_config(
            tunnel_send_rate=1.0,
            tunnel_send_burst=1.0,
            tunnel_initial_window=2,
            tunnel_adaptive_pacing_enabled=False,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())

        now = time_provider.now()
        permit = transport.reserve_send(now=now)
        limiter = alice._send_limiter
        limiter._bucket._tokens = 0.0
        limiter._bucket._last_refill = now

        alice._send_new_packet([Segment(1, b'data')], now=now, permit=permit)

        self.assertEqual(transport.release_calls, 1)
        self.assertEqual(alice._send_window.unacked_count, 0)


class TickTests(unittest.TestCase):
    def test_tick_sends_keepalive_when_due(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_keepalive_interval=0.01,
            tunnel_initial_window=2,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._set_state(TunnelState.CONNECTED)
        alice._last_send_time = 0.0
        alice._got_data = False
        alice._has_pending_data_acks = False
        alice._last_was_pong_only = False

        alice.tick()

        self.assertEqual(len(transport._pending), 1)
        packet = alice._decode_packet(transport._pending[0][1])
        self.assertIsNotNone(packet)
        self.assertTrue(packet.flags & FLAG_KEEPALIVE)
        self.assertEqual(len(packet.segments), 0)

    def test_tick_sleeps_when_idle(self):
        transport = _DummyTransport()
        config = make_test_config(
            tunnel_keepalive_interval=1000.0,
            tunnel_window_growth_enabled=False,
        )
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._set_state(TunnelState.CONNECTED)
        alice._last_send_time = time_provider.now()
        alice._got_data = False
        alice._has_pending_data_acks = False
        alice._last_was_pong_only = False

        calls = []

        def fake_sleep(duration):
            calls.append(duration)

        original_sleep = time_provider.sleep
        time_provider.sleep = fake_sleep
        try:
            alice.tick()
        finally:
            time_provider.sleep = original_sleep

        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0], 0.01)


class HandleResponseTests(unittest.TestCase):
    def _make_alice(self):
        transport = _DummyTransport()
        return _ResponseAlice(transport, make_test_config(), crypto=Plain())

    def _handle(self, alice, packet):
        data = packet.encode()
        return alice._handle_response(data, now=0.0)

    def test_pong_only_is_not_real_data(self):
        alice = self._make_alice()
        seg = Segment(CHANNEL_CONTROL, b'{"t":"tun","c":"pong"}\n')
        packet = Packet(seq=1, ack=0, sack=0, flags=0, segments=[seg])

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertFalse(has_data)
        self.assertFalse(alice._got_data)

    def test_control_message_counts_as_data(self):
        alice = self._make_alice()
        seg = Segment(CHANNEL_CONTROL, b'{"t":"tun","c":"window","size":2}\n')
        packet = Packet(seq=1, ack=0, sack=0, flags=0, segments=[seg])

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertTrue(has_data)
        self.assertTrue(alice._got_data)

    def test_data_segment_counts_as_data(self):
        alice = self._make_alice()
        seg = Segment(1, b'payload')
        packet = Packet(seq=1, ack=0, sack=0, flags=0, segments=[seg])

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertTrue(has_data)
        self.assertTrue(alice._got_data)

    def test_keepalive_packet_has_no_data(self):
        alice = self._make_alice()
        alice._set_state(TunnelState.CONNECTED)
        packet = Packet(seq=1, ack=0, sack=0, flags=FLAG_KEEPALIVE)

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertFalse(has_data)
        self.assertFalse(alice._got_data)

    def test_invalid_control_json_counts_as_data(self):
        alice = self._make_alice()
        seg = Segment(CHANNEL_CONTROL, b'{"t":"tun",\n')
        packet = Packet(seq=1, ack=0, sack=0, flags=0, segments=[seg])

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertTrue(has_data)
        self.assertTrue(alice._got_data)

    def test_multi_line_control_detects_non_pong(self):
        alice = self._make_alice()
        seg = Segment(
            CHANNEL_CONTROL,
            b'{"t":"tun","c":"pong"}\n{"t":"tun","c":"window","size":2}\n',
        )
        packet = Packet(seq=1, ack=0, sack=0, flags=0, segments=[seg])

        valid, has_data = self._handle(alice, packet)

        self.assertTrue(valid)
        self.assertTrue(has_data)
        self.assertTrue(alice._got_data)

    def test_decode_failure_returns_invalid(self):
        alice = self._make_alice()

        valid, has_data = alice._handle_response(b'', now=0.0)

        self.assertFalse(valid)
        self.assertFalse(has_data)


class HandleResponseAckTests(unittest.TestCase):
    def test_ack_progress_sets_flags_and_pacer(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._set_state(TunnelState.CONNECTED)

        now = time_provider.now()
        alice._send_window.send([Segment(1, b'data')], now=now - 1.0)
        packet = Packet(seq=0, ack=1, sack=0, flags=0)

        valid, has_data = alice._handle_response(packet.encode(), now=now)

        self.assertTrue(valid)
        self.assertFalse(has_data)
        self.assertTrue(alice._ack_progressed)
        self.assertEqual(alice._send_window.unacked_count, 0)
        self.assertEqual(alice._pacer._last_ack_time, now)


class ProcessIncomingTests(unittest.TestCase):
    def test_process_incoming_packet_dispatches_control(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._set_state(TunnelState.CONNECTED)
        alice._recv_window.set_initial_seq(0)

        seg = Segment(
            CHANNEL_CONTROL,
            b'{"t":"tun","c":"window_ok","size":4}\n',
        )
        packet = Packet(seq=0, ack=0, sack=0, flags=0, segments=[seg])

        alice._process_incoming_packet(packet, now=0.0)

        self.assertTrue(alice._window_negotiated)
        self.assertEqual(alice._negotiated_window, 4)
        self.assertEqual(alice._send_window._max_in_flight, 4)
