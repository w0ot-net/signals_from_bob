# -*- coding: ascii -*-
"""Unit tests for Bob retransmit cap-blocked behavior."""

from __future__ import absolute_import

import logging
import unittest

from sfb.protocol.constants import MIN_PACKET_MTU
from sfb.tunnel.bob_tunnel import BobTunnel


class DummySendWindow(object):
    def __init__(self, can_send=True, drop_seq=None):
        self.can_send = can_send
        self._max_in_flight = 1
        self.unacked_count = 0
        self._drop_seq = drop_seq
        self.drop_calls = []

    def drop_oldest_keepalive(self, reason=None, now=None):
        self.drop_calls.append((reason, now))
        return self._drop_seq

    def get_unacked_info(self, seq):
        raise AssertionError('get_unacked_info should not be called')

    def mark_retransmit(self, seq, now=None):
        raise AssertionError('mark_retransmit should not be called')


class BobRetransmitCapTests(unittest.TestCase):
    def _make_tunnel(self, can_send=True, drop_seq=None, response_len=0):
        tunnel = BobTunnel.__new__(BobTunnel)
        tunnel._send_window = DummySendWindow(
            can_send=can_send,
            drop_seq=drop_seq,
        )
        tunnel._last_retransmit_cap_blocked_log = None
        tunnel._logger = logging.getLogger('test')

        def rebuild_packet(seq, segments, flags=0):
            return object()

        def encode_packet_for_send(packet, encrypted_body=None):
            return (b'', b'x' * response_len)

        tunnel._rebuild_packet = rebuild_packet
        tunnel._encode_packet_for_send = encode_packet_for_send
        return tunnel

    def test_retransmit_cap_blocked_sends_keepalive_without_poll_hint(self):
        response_len = MIN_PACKET_MTU + 5
        response_payload_cap = MIN_PACKET_MTU - 1
        tunnel = self._make_tunnel(
            can_send=True,
            response_len=response_len,
        )
        called = {}

        def keepalive(responder, now, poll_hint=False,
                      response_payload_cap=None):
            called['poll_hint'] = poll_hint
            called['cap'] = response_payload_cap
            return True

        def send_response(*args, **kwargs):
            raise AssertionError('send_response_packet should not be called')

        tunnel._send_keepalive_response = keepalive
        tunnel._send_response_packet = send_response
        ok = tunnel._send_retransmit_response(
            responder=None,
            response_payload_cap=response_payload_cap,
            now=1.0,
            seq=1,
            segments=[],
            flags=0,
            encrypted_body=None,
        )
        self.assertFalse(ok)
        self.assertEqual(called.get('poll_hint'), False)
        self.assertEqual(called.get('cap'), response_payload_cap)
        self.assertEqual(tunnel._send_window.drop_calls, [])

    def test_retransmit_cap_blocked_sends_keepalive_with_poll_hint(self):
        response_len = MIN_PACKET_MTU + 5
        response_payload_cap = MIN_PACKET_MTU
        tunnel = self._make_tunnel(
            can_send=True,
            response_len=response_len,
        )
        called = {}

        def keepalive(responder, now, poll_hint=False,
                      response_payload_cap=None):
            called['poll_hint'] = poll_hint
            called['cap'] = response_payload_cap
            return True

        tunnel._send_keepalive_response = keepalive
        ok = tunnel._send_retransmit_response(
            responder=None,
            response_payload_cap=response_payload_cap,
            now=1.0,
            seq=2,
            segments=[],
            flags=0,
            encrypted_body=None,
        )
        self.assertFalse(ok)
        self.assertEqual(called.get('poll_hint'), True)
        self.assertEqual(called.get('cap'), response_payload_cap)
        self.assertEqual(tunnel._send_window.drop_calls, [])

    def test_retransmit_cap_blocked_drops_oldest_keepalive_when_full(self):
        response_len = MIN_PACKET_MTU + 5
        response_payload_cap = MIN_PACKET_MTU
        tunnel = self._make_tunnel(
            can_send=False,
            drop_seq=12,
            response_len=response_len,
        )
        called = {}
        logged = {}

        def keepalive(responder, now, poll_hint=False,
                      response_payload_cap=None):
            called['poll_hint'] = poll_hint
            called['cap'] = response_payload_cap
            return True

        def log_state(level, event, message, now=None,
                      include_buffered=False, extra_fields=None):
            logged['level'] = level
            logged['event'] = event
            logged['message'] = message
            logged['now'] = now
            logged['extra_fields'] = extra_fields or {}

        tunnel._send_keepalive_response = keepalive
        tunnel._log_reliability_state = log_state
        ok = tunnel._send_retransmit_response(
            responder=None,
            response_payload_cap=response_payload_cap,
            now=2.0,
            seq=3,
            segments=[],
            flags=0,
            encrypted_body=None,
        )
        self.assertFalse(ok)
        self.assertEqual(called.get('poll_hint'), True)
        self.assertEqual(called.get('cap'), response_payload_cap)
        self.assertEqual(
            tunnel._send_window.drop_calls,
            [('poll_hint_window_full', 2.0)],
        )
        self.assertEqual(logged.get('event'), 'tunnel.reliability_state')
        self.assertEqual(logged.get('extra_fields', {}).get('context'),
                         'poll_hint_keepalive')
        self.assertEqual(logged.get('extra_fields', {}).get('reason'),
                         'window_full')
        self.assertEqual(logged.get('extra_fields', {}).get('seq'), 12)


if __name__ == '__main__':
    unittest.main()
