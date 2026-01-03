# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.config import Config
from sfb.crypto import Plain
from sfb.protocol import Packet, Segment, CHANNEL_CONTROL, FLAG_KEEPALIVE
from sfb.tunnel import AliceTunnel, TunnelState
from sfb.transport import Transport


def make_test_config(**overrides):
    defaults = {
        'dns_base_domain': 'test.local',
    }
    defaults.update(overrides)
    return Config(**defaults)


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


class _RecordingAlice(AliceTunnel):
    def __init__(self, transport, config, crypto=None, logger=None):
        super(_RecordingAlice, self).__init__(
            transport,
            config,
            crypto=crypto,
            logger=logger,
        )
        self.retransmits = []

    def _can_send_retransmit(self, now=None):
        return True

    def _send_retransmit(self, seq, segments, flags, encrypted_body, now, reason=None):
        self.retransmits.append((seq, reason))
        if reason == 'fast_gap':
            self._fast_retransmit_sent += 1
        return True


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

    def _maybe_fast_retransmit(self, packet, now):
        return False

    def _update_fast_recovery(self, packet):
        return None


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


class FastRetransmitTests(unittest.TestCase):
    def test_fast_retransmit_caps_two_packets(self):
        transport = _DummyTransport()
        alice = _RecordingAlice(transport, make_test_config(), crypto=Plain())
        alice._send_window.send([Segment(1, b'a')], now=0.0)
        alice._send_window.send([Segment(1, b'b')], now=0.0)
        alice._send_window.send([Segment(1, b'c')], now=0.0)

        packet = Packet(seq=10, ack=0, sack=1, flags=0)
        sent = alice._maybe_fast_retransmit(packet, now=0.0)

        self.assertTrue(sent)
        self.assertEqual([item[0] for item in alice.retransmits], [0, 1])
        self.assertEqual(alice._fast_retransmit_sent, 2)
        self.assertEqual(set(alice._fast_retransmit_seqs), set([0, 1]))

    def test_fast_retransmit_resets_on_zero_sack(self):
        transport = _DummyTransport()
        alice = _RecordingAlice(transport, make_test_config(), crypto=Plain())
        alice._fast_retransmit_ack = 5
        alice._fast_retransmit_seqs = set([1, 2])

        packet = Packet(seq=10, ack=5, sack=0, flags=0)
        sent = alice._maybe_fast_retransmit(packet, now=0.0)

        self.assertFalse(sent)
        self.assertIsNone(alice._fast_retransmit_ack)
        self.assertEqual(alice._fast_retransmit_seqs, set())

    def test_fast_retransmit_empty_gap_no_send(self):
        transport = _DummyTransport()
        alice = _RecordingAlice(transport, make_test_config(), crypto=Plain())

        packet = Packet(seq=10, ack=5, sack=1, flags=0)
        sent = alice._maybe_fast_retransmit(packet, now=0.0)

        self.assertFalse(sent)
        self.assertEqual(alice.retransmits, [])
        self.assertEqual(alice._fast_retransmit_ack, 5)
        self.assertEqual(alice._fast_retransmit_seqs, set())

    def test_fast_retransmit_drops_keepalive(self):
        transport = _DummyTransport()
        alice = _RecordingAlice(transport, make_test_config(), crypto=Plain())
        alice._send_window.send([], flags=FLAG_KEEPALIVE, now=0.0)

        packet = Packet(seq=10, ack=0, sack=1, flags=0)
        sent = alice._maybe_fast_retransmit(packet, now=0.0)

        self.assertFalse(sent)
        self.assertEqual(alice._send_window.unacked_count, 0)


class SendGateTests(unittest.TestCase):
    def test_fast_recovery_blocks_new_send(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._fast_recovery_active = True
        alice._fast_recovery_ack = 0

        allowed = alice._can_send_new(now=0.0, allow_fast_recovery=False)

        self.assertFalse(allowed)

    def test_send_window_full_blocks_new_send(self):
        transport = _DummyTransport()
        config = make_test_config(tunnel_initial_window=1)
        alice = AliceTunnel(transport, config, crypto=Plain())
        alice._send_window.send([Segment(1, b'data')], now=0.0)

        allowed = alice._can_send_new(now=0.0)

        self.assertFalse(allowed)


class FastRecoveryTests(unittest.TestCase):
    def test_fast_recovery_tracks_gap_and_clears_on_ack_change(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._send_window.send([Segment(1, b'data')], now=0.0)

        gap_packet = Packet(seq=10, ack=0, sack=1, flags=0)
        alice._update_fast_recovery(gap_packet)

        self.assertTrue(alice._fast_recovery_active)
        self.assertEqual(alice._fast_recovery_ack, 0)

        clear_packet = Packet(seq=11, ack=1, sack=1, flags=0)
        alice._update_fast_recovery(clear_packet)

        self.assertFalse(alice._fast_recovery_active)
        self.assertIsNone(alice._fast_recovery_ack)

    def test_fast_recovery_clears_when_unacked_empty(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._fast_recovery_active = True
        alice._fast_recovery_ack = 0

        packet = Packet(seq=10, ack=0, sack=1, flags=0)
        alice._update_fast_recovery(packet)

        self.assertFalse(alice._fast_recovery_active)
        self.assertIsNone(alice._fast_recovery_ack)

    def test_fast_recovery_clears_on_sack_zero(self):
        transport = _DummyTransport()
        alice = AliceTunnel(transport, make_test_config(), crypto=Plain())
        alice._send_window.send([Segment(1, b'data')], now=0.0)
        alice._fast_recovery_active = True
        alice._fast_recovery_ack = 0

        packet = Packet(seq=10, ack=0, sack=0, flags=0)
        alice._update_fast_recovery(packet)

        self.assertFalse(alice._fast_recovery_active)
        self.assertIsNone(alice._fast_recovery_ack)


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
