# -*- coding: ascii -*-
"""Unit tests for Bob tunnel response-cap and poll-hint edges."""

from __future__ import absolute_import

import unittest

from sfb.protocol import Packet, FLAG_KEEPALIVE, FLAG_HAS_SEGMENTS, FLAG_POLL_HINT
from sfb.protocol.constants import MIN_PACKET_MTU, PACKET_HEADER_SIZE
from sfb.tunnel.bob_tunnel import BobTunnel


class DummySendWindow(object):
    def __init__(self, can_send=True):
        self.can_send = can_send
        self._max_in_flight = 1
        self.unacked_count = 0
        self.last_cum_ack = 0

    def get_oldest_unacked_info(self):
        return None

    def ack_silence(self, now=None):
        return None

    def distance_exceeded(self, max_window=None):
        return (False, None)


class CollectSegmentsSpy(object):
    def __init__(self, segments, pending_data):
        self._segments = list(segments)
        self._pending_data = pending_data
        self.calls = []

    def __call__(self, max_payload, return_pending=False, control_only=False):
        self.calls.append((max_payload, return_pending, control_only))
        if return_pending:
            return (list(self._segments), self._pending_data)
        return list(self._segments)


class BobTunnelClampingTests(unittest.TestCase):
    def _make_tunnel(self):
        tunnel = BobTunnel.__new__(BobTunnel)
        tunnel._send_window = DummySendWindow()
        tunnel._send_packet_mtu = PACKET_HEADER_SIZE + 50
        tunnel._poll_interval_ewma = None
        tunnel._retransmit_cooldown = lambda: 0.0
        return tunnel

    def _install_packet_spies(self, tunnel, captured):
        def build_packet(flags=0, segments=None):
            return Packet(flags=flags), 0

        def encode_packet_for_send(packet, encrypted_body=None):
            return (b'', b'x')

        def send_response_packet(responder, packet, response_data, *args,
                                 **kwargs):
            captured['flags'] = packet.flags
            captured['segments'] = list(packet.segments)
            return True

        tunnel._build_packet = build_packet
        tunnel._encode_packet_for_send = encode_packet_for_send
        tunnel._send_response_packet = send_response_packet

    def test_select_response_action_keepalive_when_poll_hint_disallowed(self):
        tunnel = self._make_tunnel()
        collector = CollectSegmentsSpy([], True)
        tunnel._collect_segments = collector
        response_payload_cap = MIN_PACKET_MTU - 1
        decision = tunnel._select_response_action(0.0, response_payload_cap)
        self.assertEqual(decision.get('action'), 'keepalive')
        expected_cap = response_payload_cap - PACKET_HEADER_SIZE
        self.assertEqual(collector.calls, [(expected_cap, True, False)])

    def test_select_response_action_poll_hint_when_pending_and_allowed(self):
        tunnel = self._make_tunnel()
        collector = CollectSegmentsSpy([], True)
        tunnel._collect_segments = collector
        decision = tunnel._select_response_action(0.0, None)
        self.assertEqual(decision.get('action'), 'poll_hint')
        self.assertEqual(collector.calls, [(50, True, False)])

    def test_send_keepalive_disables_poll_hint_when_cap_too_small(self):
        tunnel = self._make_tunnel()
        captured = {}
        self._install_packet_spies(tunnel, captured)
        ok = tunnel._send_keepalive_response(
            responder=None,
            now=0.0,
            poll_hint=True,
            response_payload_cap=MIN_PACKET_MTU - 1,
        )
        self.assertTrue(ok)
        self.assertEqual(captured.get('flags'), FLAG_KEEPALIVE)

    def test_send_keepalive_allows_poll_hint_when_cap_is_none(self):
        tunnel = self._make_tunnel()
        captured = {}
        self._install_packet_spies(tunnel, captured)
        ok = tunnel._send_keepalive_response(
            responder=None,
            now=0.0,
            poll_hint=True,
            response_payload_cap=None,
        )
        self.assertTrue(ok)
        self.assertEqual(
            captured.get('flags'),
            FLAG_KEEPALIVE | FLAG_POLL_HINT,
        )

    def test_send_poll_hint_segment_falls_back_when_cap_too_small(self):
        tunnel = self._make_tunnel()
        called = {}

        def keepalive(responder, now, poll_hint=False,
                      response_payload_cap=None):
            called['poll_hint'] = poll_hint
            called['cap'] = response_payload_cap
            return 'ok'

        def fail_collect(*args, **kwargs):
            raise AssertionError('collect_segments should not run')

        tunnel._send_keepalive_response = keepalive
        tunnel._collect_segments = fail_collect
        result = tunnel._send_poll_hint_segment(
            responder=None,
            now=0.0,
            response_payload_cap=MIN_PACKET_MTU - 1,
        )
        self.assertEqual(result, 'ok')
        self.assertFalse(called.get('poll_hint'))
        self.assertEqual(called.get('cap'), MIN_PACKET_MTU - 1)

    def test_send_poll_hint_segment_keepalive_when_no_segments(self):
        tunnel = self._make_tunnel()
        collector = CollectSegmentsSpy([], False)
        tunnel._collect_segments = collector
        called = {}

        def keepalive(responder, now, poll_hint=False,
                      response_payload_cap=None):
            called['poll_hint'] = poll_hint
            called['cap'] = response_payload_cap
            return True

        tunnel._send_keepalive_response = keepalive
        result = tunnel._send_poll_hint_segment(
            responder=None,
            now=0.0,
            response_payload_cap=None,
        )
        self.assertTrue(result)
        self.assertTrue(called.get('poll_hint'))
        self.assertIsNone(called.get('cap'))
        self.assertEqual(collector.calls, [(50, False, True)])

    def test_send_poll_hint_segment_sends_control_poll_hint(self):
        tunnel = self._make_tunnel()
        collector = CollectSegmentsSpy(['seg'], False)
        tunnel._collect_segments = collector
        captured = {}
        self._install_packet_spies(tunnel, captured)
        result = tunnel._send_poll_hint_segment(
            responder=None,
            now=0.0,
            response_payload_cap=None,
        )
        self.assertTrue(result)
        self.assertEqual(
            captured.get('flags'),
            FLAG_HAS_SEGMENTS | FLAG_POLL_HINT,
        )
        self.assertEqual(collector.calls, [(50, False, True)])


if __name__ == '__main__':
    unittest.main()
