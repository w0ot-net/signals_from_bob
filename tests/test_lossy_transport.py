# -*- coding: ascii -*-
"""Tests for lossy transport wrappers."""

from __future__ import absolute_import

import unittest

from sfb.transport import (
    Transport,
    Server,
    NetworkImpairment,
    LossyTransport,
    LossyServer,
    no_impairment,
    high_latency,
    moderate_loss,
    heavy_loss,
    burst_loss,
    extreme_conditions,
    chaos,
)
from sfb import time_provider


class MockTransport(Transport):
    """Simple mock transport for testing."""

    def __init__(self):
        self._next_corr_id = 0
        self._pending = {}  # corr_id -> response
        self._sent = []

    def send(self, data):
        corr_id = self._next_corr_id
        self._next_corr_id += 1
        self._sent.append(data)
        # Echo back the data as response
        self._pending[corr_id] = data
        return corr_id

    def recv(self, timeout=None):
        if self._pending:
            corr_id = min(self._pending.keys())
            data = self._pending.pop(corr_id)
            return (corr_id, data)
        return (None, None)

    def pending_count(self):
        return len(self._pending)

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
        self._pending.clear()


class MockServer(Server):
    """Simple mock server for testing."""

    def __init__(self):
        self._requests = []
        self._responses = []

    def inject_request(self, data):
        """Inject a request to be received."""
        self._requests.append(data)

    def recv(self, timeout=None):
        if self._requests:
            data = self._requests.pop(0)
            return (data, self._make_responder())
        return (None, None)

    def _make_responder(self):
        def responder(data):
            self._responses.append(data)
        return responder

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self._requests.clear()
        self._responses.clear()


class NetworkImpairmentTests(unittest.TestCase):
    """Tests for NetworkImpairment configuration."""

    def test_no_impairment_never_drops(self):
        imp = NetworkImpairment(seed=42)
        for _ in range(100):
            self.assertFalse(imp.should_drop())

    def test_full_loss_always_drops(self):
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        for _ in range(100):
            self.assertTrue(imp.should_drop())

    def test_partial_loss_rate(self):
        imp = NetworkImpairment(loss_rate=0.5, seed=42)
        drops = sum(1 for _ in range(1000) if imp.should_drop())
        # Should be roughly 50% with some variance
        self.assertGreater(drops, 400)
        self.assertLess(drops, 600)

    def test_burst_loss(self):
        imp = NetworkImpairment(
            burst_loss_prob=1.0,
            burst_loss_len=(5, 5),
            seed=42
        )
        # First call triggers burst
        self.assertTrue(imp.should_drop())
        # Next 4 are in burst
        for _ in range(4):
            self.assertTrue(imp.should_drop())
        # After burst, need to trigger again
        # (probability is 1.0 so next call starts new burst)

    def test_delay_calculation(self):
        imp = NetworkImpairment(delay_ms=100, jitter_ms=0, seed=42)
        delay = imp.get_delay_sec()
        self.assertAlmostEqual(delay, 0.1, places=3)

    def test_jitter_adds_variance(self):
        imp = NetworkImpairment(delay_ms=100, jitter_ms=50, seed=42)
        delays = [imp.get_delay_sec() for _ in range(100)]
        # All delays should be in range [50ms, 150ms]
        for d in delays:
            self.assertGreaterEqual(d, 0.05)
            self.assertLessEqual(d, 0.15)
        # Should have some variance
        self.assertGreater(max(delays) - min(delays), 0.01)

    def test_stats_tracking(self):
        imp = NetworkImpairment(loss_rate=0.5, dup_rate=0.5, seed=42)
        for _ in range(100):
            imp.should_drop()
            imp.should_duplicate()

        stats = imp.stats()
        self.assertGreater(stats['dropped'], 0)
        self.assertGreater(stats['duplicated'], 0)

    def test_stats_reset(self):
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        imp.should_drop()
        self.assertEqual(imp.packets_dropped, 1)
        imp.reset_stats()
        self.assertEqual(imp.packets_dropped, 0)


class LossyTransportTests(unittest.TestCase):
    """Tests for LossyTransport wrapper."""

    def test_passthrough_no_impairment(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        corr_id = lossy.send(b'test')
        result = lossy.recv(timeout=0)

        self.assertEqual(result, (corr_id, b'test'))

    def test_full_loss_drops_all(self):
        inner = MockTransport()
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyTransport(inner, imp)

        # Send should "succeed" but packet is dropped
        corr_id = lossy.send(b'test')
        self.assertIsNotNone(corr_id)

        # Inner transport should not have received anything
        self.assertEqual(len(inner._sent), 0)

        # recv should return nothing
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

    def test_corruption_drops_packet(self):
        """Corruption simulates lower-layer discard (packet dropped, not modified)."""
        inner = MockTransport()
        imp = NetworkImpairment(corrupt_rate=1.0, corrupt_bytes=(1, 1), seed=42)
        lossy = LossyTransport(inner, imp)

        lossy.send(b'\x00' * 10)

        # Corrupted packets are dropped, not sent to inner
        self.assertEqual(len(inner._sent), 0)

    def test_duplication(self):
        inner = MockTransport()
        imp = NetworkImpairment(dup_rate=1.0, seed=42)
        lossy = LossyTransport(inner, imp)

        lossy.send(b'test')

        # Should have sent twice to inner
        self.assertEqual(len(inner._sent), 2)

    def test_pending_count_includes_dropped(self):
        inner = MockTransport()
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyTransport(inner, imp)

        lossy.send(b'test')

        # Dropped packet should count as pending
        self.assertEqual(lossy.pending_count(), 1)

    def test_delay_buffers_response(self):
        inner = MockTransport()
        imp = NetworkImpairment(delay_ms=100, seed=42)
        lossy = LossyTransport(inner, send_impairment=no_impairment(),
                               recv_impairment=imp)

        corr_id = lossy.send(b'test')

        # Immediate recv should return nothing (delayed)
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

        # After delay, should return
        time_provider.sleep(0.15)
        result = lossy.recv(timeout=0)
        self.assertEqual(result[0], corr_id)

    def test_stats_accessible(self):
        inner = MockTransport()
        imp = NetworkImpairment(loss_rate=0.5, seed=42)
        lossy = LossyTransport(inner, imp)

        for _ in range(10):
            lossy.send(b'test')

        stats = lossy.stats()
        self.assertIn('send', stats)
        self.assertIn('recv', stats)

    def test_mtu_passthrough(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        self.assertEqual(lossy.send_mtu, inner.send_mtu)
        self.assertEqual(lossy.recv_mtu, inner.recv_mtu)
        self.assertEqual(lossy.max_pending, inner.max_pending)


class LossyServerTests(unittest.TestCase):
    """Tests for LossyServer wrapper."""

    def test_passthrough_no_impairment(self):
        inner = MockServer()
        lossy = LossyServer(inner, no_impairment())

        inner.inject_request(b'request')
        data, responder = lossy.recv(timeout=0)

        self.assertEqual(data, b'request')
        responder(b'response')
        self.assertEqual(inner._responses, [b'response'])

    def test_recv_loss_drops_request(self):
        inner = MockServer()
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyServer(inner, recv_impairment=imp)

        inner.inject_request(b'request')
        result = lossy.recv(timeout=0)

        self.assertEqual(result, (None, None))

    def test_send_loss_drops_response(self):
        inner = MockServer()
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyServer(inner, recv_impairment=no_impairment(),
                           send_impairment=imp)

        inner.inject_request(b'request')
        data, responder = lossy.recv(timeout=0)

        responder(b'response')
        # Response should be dropped
        self.assertEqual(inner._responses, [])

    def test_corruption_drops_request(self):
        """Corruption simulates lower-layer discard (request dropped, not modified)."""
        inner = MockServer()
        imp = NetworkImpairment(corrupt_rate=1.0, corrupt_bytes=(1, 1), seed=42)
        lossy = LossyServer(inner, recv_impairment=imp)

        inner.inject_request(b'\x00' * 10)
        result = lossy.recv(timeout=0)

        # Corrupted request is dropped
        self.assertEqual(result, (None, None))

    def test_stats_accessible(self):
        inner = MockServer()
        imp = NetworkImpairment(loss_rate=0.5, seed=42)
        lossy = LossyServer(inner, imp)

        for _ in range(10):
            inner.inject_request(b'test')
            lossy.recv(timeout=0)

        stats = lossy.stats()
        self.assertIn('recv', stats)
        self.assertIn('send', stats)


class PresetTests(unittest.TestCase):
    """Tests for convenience presets."""

    def test_no_impairment_preset(self):
        imp = no_impairment()
        self.assertEqual(imp.loss_rate, 0)
        self.assertEqual(imp.delay_ms, 0)

    def test_high_latency_preset(self):
        imp = high_latency()
        self.assertEqual(imp.delay_ms, 500)
        self.assertEqual(imp.jitter_ms, 100)
        self.assertEqual(imp.loss_rate, 0)

    def test_moderate_loss_preset(self):
        imp = moderate_loss()
        self.assertEqual(imp.loss_rate, 0.15)
        self.assertGreater(imp.delay_ms, 0)

    def test_heavy_loss_preset(self):
        imp = heavy_loss()
        self.assertEqual(imp.loss_rate, 0.40)

    def test_burst_loss_preset(self):
        imp = burst_loss()
        self.assertGreater(imp.burst_loss_prob, 0)

    def test_extreme_conditions_preset(self):
        imp = extreme_conditions()
        self.assertEqual(imp.loss_rate, 0.50)
        self.assertGreater(imp.reorder_rate, 0)

    def test_chaos_preset(self):
        imp = chaos()
        self.assertGreater(imp.loss_rate, 0)
        self.assertGreater(imp.burst_loss_prob, 0)
        self.assertGreater(imp.dup_rate, 0)
        self.assertGreater(imp.reorder_rate, 0)
        self.assertGreater(imp.corrupt_rate, 0)

    def test_presets_accept_seed(self):
        imp1 = chaos(seed=42)
        imp2 = chaos(seed=42)
        # Same seed should give same random sequence
        drops1 = [imp1.should_drop() for _ in range(10)]
        drops2 = [imp2.should_drop() for _ in range(10)]
        self.assertEqual(drops1, drops2)


if __name__ == '__main__':
    unittest.main()
