# -*- coding: ascii -*-
"""Integration tests for channel race conditions using in-memory transport."""

from __future__ import absolute_import

import threading
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.crypto import Plain
from sfb.protocol import CHANNEL_CONTROL
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState
from sfb.transport import create_inmemory_transport_pair


def make_test_config(**overrides):
    """Create a Config for integration tests with sensible defaults."""
    defaults = {
        'dns_base_domain': 'test.local',
        'tunnel_idle_timeout': 10.0,
        'tunnel_keepalive_interval': 5.0,
        'max_in_flight': 16,
        'tunnel_connect_timeout': 2.0,
        'tunnel_no_response_timeout': 50.0,
    }
    defaults.update(overrides)
    return Config(**defaults)


def _drive_until(alice, predicate, timeout):
    deadline = time_provider.now() + timeout
    while time_provider.now() < deadline:
        if predicate():
            return True
        alice.tick()
        time_provider.sleep(0.01)
    return False


class _OpenDataBob(BobTunnel):
    def __init__(self, server, config, crypto=None, logger=None):
        BobTunnel.__init__(self, server, config, crypto=crypto, logger=logger)
        self._open_data_channel_id = None
        self._open_data_payload = None
        self._open_data_sent = False
        self.open_data_combined = False

    def arm_open_data(self, channel_id, payload):
        self._open_data_channel_id = channel_id
        self._open_data_payload = payload

    def _collect_segments(self, max_payload, keepalive_data=None,
                          return_pending=False, control_only=False):
        result = BobTunnel._collect_segments(
            self,
            max_payload,
            keepalive_data=keepalive_data,
            return_pending=return_pending,
            control_only=control_only,
        )
        segments = result[0] if return_pending else result
        if self._open_data_channel_id is not None and segments:
            has_control = False
            has_data = False
            for segment in segments:
                if segment.channel == CHANNEL_CONTROL:
                    has_control = True
                elif segment.channel == self._open_data_channel_id:
                    has_data = True
            if has_control and has_data:
                self.open_data_combined = True
        return result

    def _handle_data(self, packet, responder, now, packet_size=None):
        self._process_incoming_packet(packet, now=now, packet_size=packet_size)
        if (not self._open_data_sent and
                self._open_data_channel_id is not None):
            channel = self.channel_manager.get_channel(self._open_data_channel_id)
            if channel is not None and channel.is_open:
                channel.write(self._open_data_payload)
                self._open_data_sent = True
        self._send_response(responder, now)


class OpenOkDataIntegrationTests(unittest.TestCase):
    def test_open_ok_and_data_share_packet(self):
        config = make_test_config()
        alice_transport, bob_server = create_inmemory_transport_pair(config)
        alice = AliceTunnel(alice_transport, config, crypto=Plain())
        bob = _OpenDataBob(bob_server, config, crypto=Plain())

        bob_thread = threading.Thread(target=bob.serve_forever)
        bob_thread.daemon = True
        bob_thread.start()

        try:
            alice.connect(timeout=2.0)
            self.assertEqual(alice.state, TunnelState.CONNECTED)

            channel = alice.channel_manager.open_channel()
            payload = b'hello'
            bob.arm_open_data(channel.id, payload)

            opened = _drive_until(alice, lambda: channel.is_open, 1.0)
            self.assertTrue(opened)

            got_data = _drive_until(
                alice,
                lambda: channel.recv_buf_size > 0,
                1.0,
            )
            self.assertTrue(got_data)
            self.assertEqual(channel.read(len(payload), timeout=0.1), payload)
            self.assertTrue(bob.open_data_combined)
        finally:
            alice.close()
            bob.close()
            bob_thread.join(timeout=1.0)


class SendStateReuseIntegrationTests(unittest.TestCase):
    def test_stale_send_state_callback_ignored_after_reuse(self):
        config = make_test_config(channel_id_reuse_cooldown=0)
        alice_transport, bob_server = create_inmemory_transport_pair(config)
        alice = AliceTunnel(alice_transport, config, crypto=Plain())
        bob = BobTunnel(bob_server, config, crypto=Plain())

        bob_thread = threading.Thread(target=bob.serve_forever)
        bob_thread.daemon = True
        bob_thread.start()

        try:
            alice.connect(timeout=2.0)
            self.assertEqual(alice.state, TunnelState.CONNECTED)

            channel = alice.channel_manager.open_channel()
            opened = _drive_until(alice, lambda: channel.is_open, 1.0)
            self.assertTrue(opened)

            old_callback = channel._send_state_callback
            old_channel_id = channel.id
            channel.abort()

            removed = _drive_until(
                alice,
                lambda: alice.channel_manager.get_channel(old_channel_id) is None,
                1.0,
            )
            self.assertTrue(removed)

            alice.channel_manager._next_channel_id = old_channel_id
            new_channel = alice.channel_manager.open_channel()
            reopened = _drive_until(alice, lambda: new_channel.is_open, 1.0)
            self.assertTrue(reopened)
            self.assertEqual(new_channel.id, old_channel_id)
            self.assertTrue(
                old_channel_id not in alice.channel_manager._active_channels
            )

            old_callback(old_channel_id, True, 999)
            self.assertTrue(
                old_channel_id not in alice.channel_manager._active_channels
            )
        finally:
            alice.close()
            bob.close()
            bob_thread.join(timeout=1.0)


if __name__ == '__main__':
    unittest.main()
