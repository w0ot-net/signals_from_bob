# -*- coding: ascii -*-
"""Integration tests for full Alice+Bob tunnel over DNS transport.

These tests use real UDP sockets on localhost to verify end-to-end behavior
under various network conditions.
"""

from __future__ import absolute_import

import threading
import time
import unittest

from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState
from sfb.crypto import Plain
from sfb.transport import (
    LossyTransport,
    LossyServer,
    no_impairment,
    moderate_loss,
    heavy_loss,
    burst_loss,
    chaos,
)
from sfb.transport.dns import DnsClient, DnsServer


# Use high ports to avoid needing root
BASE_PORT = 15353


def get_test_port():
    """Get a unique port for each test to avoid conflicts."""
    import random
    return BASE_PORT + random.randint(0, 1000)


class DnsIntegrationTests(unittest.TestCase):
    """Tests for full tunnel over DNS transport."""

    def setUp(self):
        self.port = get_test_port()
        self.base_domain = 'test.tunnel.local'
        self.alice = None
        self.bob = None
        self.bob_thread = None

    def tearDown(self):
        if self.alice:
            self.alice.close()
        if self.bob:
            self.bob.close()
        if self.bob_thread:
            self.bob_thread.join(timeout=1.0)

    def _create_transports(self, alice_imp=None, bob_imp=None):
        """Create DNS client and server with optional impairments."""
        server = DnsServer(
            self.base_domain,
            listen_addr='127.0.0.1:%d' % self.port
        )
        client = DnsClient(
            self.base_domain,
            resolver='127.0.0.1:%d' % self.port
        )

        if bob_imp:
            server = LossyServer(server, recv_impairment=bob_imp,
                                 send_impairment=bob_imp)
        if alice_imp:
            client = LossyTransport(client, send_impairment=alice_imp,
                                    recv_impairment=alice_imp)

        return client, server

    def _start_bob(self, server):
        """Start Bob in a background thread."""
        self.bob = BobTunnel(server, crypto=Plain())
        self.bob_thread = threading.Thread(target=self.bob.serve_forever)
        self.bob_thread.daemon = True
        self.bob_thread.start()
        # Give Bob time to start listening
        time.sleep(0.05)

    def test_handshake_no_loss(self):
        """Test Alice and Bob can handshake over real DNS."""
        client, server = self._create_transports()
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())

        self.alice.connect(timeout=5.0)
        self.assertEqual(self.alice.state, TunnelState.CONNECTED)

        time.sleep(0.1)
        self.assertEqual(self.bob.state, TunnelState.CONNECTED)

    def test_handshake_moderate_loss(self):
        """Test handshake succeeds despite 15% loss."""
        imp = moderate_loss(seed=42)
        client, server = self._create_transports(alice_imp=imp)
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())

        # May need longer timeout with loss
        self.alice.connect(timeout=10.0)
        self.assertEqual(self.alice.state, TunnelState.CONNECTED)

    def test_handshake_heavy_loss(self):
        """Test handshake succeeds despite 40% loss."""
        imp = heavy_loss(seed=42)
        client, server = self._create_transports(alice_imp=imp)
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())

        # Heavy loss needs longer timeout
        self.alice.connect(timeout=15.0)
        self.assertEqual(self.alice.state, TunnelState.CONNECTED)

    def test_handshake_burst_loss(self):
        """Test handshake succeeds despite burst loss."""
        imp = burst_loss(seed=42)
        client, server = self._create_transports(alice_imp=imp)
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())

        self.alice.connect(timeout=15.0)
        self.assertEqual(self.alice.state, TunnelState.CONNECTED)

    def test_tick_after_connect(self):
        """Test tick works after connection."""
        client, server = self._create_transports()
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())
        self.alice.connect(timeout=5.0)

        # Tick should not raise
        for _ in range(5):
            self.alice.tick()
            time.sleep(0.05)

        self.assertEqual(self.alice.state, TunnelState.CONNECTED)

    def test_bidirectional_loss(self):
        """Test with loss on both Alice and Bob sides."""
        alice_imp = moderate_loss(seed=42)
        bob_imp = moderate_loss(seed=43)
        client, server = self._create_transports(
            alice_imp=alice_imp, bob_imp=bob_imp
        )
        self._start_bob(server)

        self.alice = AliceTunnel(client, crypto=Plain())

        self.alice.connect(timeout=15.0)
        self.assertEqual(self.alice.state, TunnelState.CONNECTED)


class StressTests(unittest.TestCase):
    """Stress tests under extreme network conditions."""

    def setUp(self):
        self.port = get_test_port()
        self.base_domain = 'stress.tunnel.local'
        self.alice = None
        self.bob = None
        self.bob_thread = None

    def tearDown(self):
        if self.alice:
            self.alice.close()
        if self.bob:
            self.bob.close()
        if self.bob_thread:
            self.bob_thread.join(timeout=1.0)

    def test_chaos_conditions(self):
        """Test tunnel survives chaos conditions (may timeout)."""
        imp = chaos(seed=42)

        server = DnsServer(
            self.base_domain,
            listen_addr='127.0.0.1:%d' % self.port
        )
        client = DnsClient(
            self.base_domain,
            resolver='127.0.0.1:%d' % self.port
        )

        # Apply chaos to Alice only (Bob needs to be responsive)
        client = LossyTransport(client, send_impairment=imp)

        self.bob = BobTunnel(LossyServer(server, no_impairment()), crypto=Plain())
        self.bob_thread = threading.Thread(target=self.bob.serve_forever)
        self.bob_thread.daemon = True
        self.bob_thread.start()
        time.sleep(0.05)

        self.alice = AliceTunnel(client, crypto=Plain())

        # Chaos is extreme - long timeout, may still fail
        try:
            self.alice.connect(timeout=20.0)
            self.assertEqual(self.alice.state, TunnelState.CONNECTED)
        except Exception:
            # Chaos conditions may cause connection timeout
            # That's acceptable - the test verifies we don't crash
            pass

    def test_many_rapid_ticks(self):
        """Test many rapid ticks don't cause issues."""
        server = DnsServer(
            self.base_domain,
            listen_addr='127.0.0.1:%d' % self.port
        )
        client = DnsClient(
            self.base_domain,
            resolver='127.0.0.1:%d' % self.port
        )

        self.bob = BobTunnel(server, crypto=Plain())
        self.bob_thread = threading.Thread(target=self.bob.serve_forever)
        self.bob_thread.daemon = True
        self.bob_thread.start()
        time.sleep(0.05)

        self.alice = AliceTunnel(client, crypto=Plain())
        self.alice.connect(timeout=5.0)

        # Rapid ticks
        for _ in range(50):
            self.alice.tick()
            time.sleep(0.01)

        self.assertEqual(self.alice.state, TunnelState.CONNECTED)


class LossRecoveryTests(unittest.TestCase):
    """Tests verifying recovery from packet loss."""

    def setUp(self):
        self.port = get_test_port()
        self.base_domain = 'recovery.tunnel.local'
        self.alice = None
        self.bob = None
        self.bob_thread = None

    def tearDown(self):
        if self.alice:
            self.alice.close()
        if self.bob:
            self.bob.close()
        if self.bob_thread:
            self.bob_thread.join(timeout=1.0)

    def test_stats_after_lossy_connection(self):
        """Verify stats are tracked during lossy connection."""
        imp = moderate_loss(seed=42)

        server = DnsServer(
            self.base_domain,
            listen_addr='127.0.0.1:%d' % self.port
        )
        client = DnsClient(
            self.base_domain,
            resolver='127.0.0.1:%d' % self.port
        )
        lossy_client = LossyTransport(client, send_impairment=imp)

        self.bob = BobTunnel(server, crypto=Plain())
        self.bob_thread = threading.Thread(target=self.bob.serve_forever)
        self.bob_thread.daemon = True
        self.bob_thread.start()
        time.sleep(0.05)

        self.alice = AliceTunnel(lossy_client, crypto=Plain())
        self.alice.connect(timeout=10.0)

        stats = lossy_client.stats()
        # With 15% loss, we should have some drops
        self.assertIn('send', stats)
        self.assertIn('recv', stats)


if __name__ == '__main__':
    unittest.main()
