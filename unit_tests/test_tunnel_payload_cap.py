# -*- coding: ascii -*-
"""Unit tests for payload cap clamping in BaseTunnel."""

from __future__ import absolute_import

import unittest

from sfb.protocol.constants import PACKET_HEADER_SIZE
from sfb.tunnel.base_tunnel import BaseTunnel


class DummyChannelManager(object):
    def __init__(self):
        self.calls = []
        self.return_value = ['seg']
        self.pending_value = False

    def collect_segments(self, max_payload, return_pending=False,
                         control_only=False):
        self.calls.append((max_payload, return_pending, control_only))
        if return_pending:
            return (list(self.return_value), self.pending_value)
        return list(self.return_value)


class BaseTunnelPayloadCapTests(unittest.TestCase):
    def _make_tunnel(self):
        tunnel = BaseTunnel.__new__(BaseTunnel)
        tunnel._channel_manager = DummyChannelManager()
        return tunnel

    def test_collect_segments_without_payload_cap(self):
        tunnel = self._make_tunnel()
        result = tunnel._collect_segments(100)
        self.assertEqual(result, ['seg'])
        self.assertEqual(tunnel._channel_manager.calls, [(100, False, False)])

    def test_collect_segments_clamps_to_payload_cap(self):
        tunnel = self._make_tunnel()
        payload_cap = PACKET_HEADER_SIZE + 5
        tunnel._collect_segments(100, payload_cap=payload_cap)
        self.assertEqual(
            tunnel._channel_manager.calls,
            [(5, False, False)],
        )

    def test_collect_segments_clamps_to_zero_when_header_only(self):
        tunnel = self._make_tunnel()
        payload_cap = PACKET_HEADER_SIZE
        tunnel._collect_segments(100, payload_cap=payload_cap)
        self.assertEqual(
            tunnel._channel_manager.calls,
            [(0, False, False)],
        )

    def test_collect_segments_clamps_to_zero_when_payload_cap_too_small(self):
        tunnel = self._make_tunnel()
        payload_cap = PACKET_HEADER_SIZE - 1
        tunnel._collect_segments(100, payload_cap=payload_cap)
        self.assertEqual(
            tunnel._channel_manager.calls,
            [(0, False, False)],
        )

    def test_collect_segments_does_not_increase_max_payload(self):
        tunnel = self._make_tunnel()
        payload_cap = PACKET_HEADER_SIZE + 200
        tunnel._collect_segments(100, payload_cap=payload_cap)
        self.assertEqual(
            tunnel._channel_manager.calls,
            [(100, False, False)],
        )

    def test_collect_segments_return_pending_passthrough(self):
        tunnel = self._make_tunnel()
        tunnel._channel_manager.pending_value = True
        payload_cap = PACKET_HEADER_SIZE + 10
        result = tunnel._collect_segments(
            10,
            return_pending=True,
            control_only=True,
            payload_cap=payload_cap,
        )
        self.assertEqual(result, (['seg'], True))
        self.assertEqual(
            tunnel._channel_manager.calls,
            [(10, True, True)],
        )


if __name__ == '__main__':
    unittest.main()
