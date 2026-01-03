# -*- coding: ascii -*-
"""Tests for lossy transport wrappers."""

from __future__ import absolute_import

import unittest

from sfb.transport import (
    Transport,
    Server,
    TransportError,
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
from sfb.transport.lossy import _ImpairmentEngine
from sfb import time_provider


class MockTransport(Transport):
    """Simple mock transport for testing."""

    def __init__(self):
        super(MockTransport, self).__init__()
        self._next_corr_id = 0
        self._pending = {}  # corr_id -> response
        self._sent = []
        self.reserve_calls = 0
        self.release_calls = 0
        self.close_calls = 0

    def reserve_send(self, now=None):
        self.reserve_calls += 1
        pending_before = self.pending_count()
        self._ensure_reserved()
        reserved = len(self._reserved)
        if pending_before + reserved >= self.max_in_flight:
            return None
        return self._reserve_permit(now=now, pending_before=pending_before)

    def release_send(self, permit):
        result = Transport.release_send(self, permit)
        self.release_calls += 1
        return result

    def _send_impl(self, data, permit):
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
    def max_in_flight(self):
        return 16

    @property
    def send_mtu(self):
        return 200

    @property
    def recv_mtu(self):
        return 200

    def close(self):
        self.close_calls += 1
        self._pending.clear()


class MockServer(Server):
    """Simple mock server for testing."""

    def __init__(self):
        self._requests = []
        self._responses = []
        self.close_calls = 0

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
        self.close_calls += 1
        self._requests.clear()
        self._responses.clear()


class ImpairmentEngineTests(unittest.TestCase):
    """Tests for impairment decisions."""

    def test_invalid_corrupt_mode_raises(self):
        with self.assertRaises(ValueError):
            NetworkImpairment(corrupt_mode='invalid')

    def test_no_impairment_never_drops(self):
        engine = _ImpairmentEngine(NetworkImpairment(seed=42))
        for _ in range(100):
            decision = engine.decide()
            self.assertFalse(decision.drop)

    def test_full_loss_always_drops(self):
        engine = _ImpairmentEngine(NetworkImpairment(loss_rate=1.0, seed=42))
        for _ in range(100):
            decision = engine.decide()
            self.assertTrue(decision.drop)

    def test_partial_loss_rate(self):
        engine = _ImpairmentEngine(NetworkImpairment(loss_rate=0.5, seed=42))
        drops = sum(1 for _ in range(1000) if engine.decide().drop)
        self.assertGreater(drops, 400)
        self.assertLess(drops, 600)

    def test_burst_loss(self):
        engine = _ImpairmentEngine(NetworkImpairment(
            burst_loss_prob=1.0,
            burst_loss_len=(5, 5),
            seed=42,
        ))
        self.assertTrue(engine.decide().drop)
        for _ in range(4):
            self.assertTrue(engine.decide().drop)

    def test_delay_calculation(self):
        engine = _ImpairmentEngine(NetworkImpairment(delay_ms=100, seed=42))
        decision = engine.decide()
        self.assertAlmostEqual(decision.delay_sec, 0.1, places=3)

    def test_jitter_adds_variance(self):
        engine = _ImpairmentEngine(NetworkImpairment(delay_ms=100, jitter_ms=50, seed=42))
        delays = [engine.decide().delay_sec for _ in range(100)]
        for delay in delays:
            self.assertGreaterEqual(delay, 0.05)
            self.assertLessEqual(delay, 0.15)
        self.assertGreater(max(delays) - min(delays), 0.01)

    def test_stats_tracking(self):
        engine = _ImpairmentEngine(NetworkImpairment(loss_rate=0.5, dup_rate=0.5, seed=42))
        for _ in range(100):
            engine.decide()
        stats = engine.stats()
        self.assertEqual(stats['sent'], 100)
        self.assertGreater(stats['dropped'], 0)
        self.assertGreater(stats['duplicated'], 0)

    def test_stats_reset(self):
        engine = _ImpairmentEngine(NetworkImpairment(loss_rate=1.0, seed=42))
        engine.decide()
        self.assertEqual(engine.stats()['dropped'], 1)
        engine.reset_stats()
        self.assertEqual(engine.stats()['dropped'], 0)

    def test_seed_determinism(self):
        config = NetworkImpairment(
            loss_rate=0.2,
            delay_ms=50,
            jitter_ms=10,
            dup_rate=0.1,
            reorder_rate=0.2,
            seed=7,
        )
        engine_a = _ImpairmentEngine(config)
        engine_b = _ImpairmentEngine(config)
        decisions_a = []
        decisions_b = []
        for _ in range(25):
            decision = engine_a.decide()
            decisions_a.append((
                decision.drop,
                decision.corrupt,
                decision.delay_sec,
                decision.duplicate_count,
                decision.reorder,
            ))
            decision = engine_b.decide()
            decisions_b.append((
                decision.drop,
                decision.corrupt,
                decision.delay_sec,
                decision.duplicate_count,
                decision.reorder,
            ))
        self.assertEqual(decisions_a, decisions_b)

    def test_corrupt_bytes_empty_payload(self):
        engine = _ImpairmentEngine(NetworkImpairment(seed=3))
        self.assertEqual(engine.corrupt_bytes(b''), b'')

    def test_corrupt_bytes_negative_and_zero_max(self):
        engine = _ImpairmentEngine(NetworkImpairment(corrupt_bytes=(-5, -1), seed=2))
        data = b'abc'
        self.assertEqual(engine.corrupt_bytes(data), data)

    def test_corrupt_bytes_clamps_max_below_min(self):
        engine = _ImpairmentEngine(NetworkImpairment(corrupt_bytes=(1, 0), seed=5))
        data = b'\x00'
        self.assertNotEqual(engine.corrupt_bytes(data), data)

    def test_corrupt_bytes_zero_count_no_mutation(self):
        class _ZeroRand(object):
            def randint(self, _a, _b):
                return 0
        engine = _ImpairmentEngine(NetworkImpairment(corrupt_bytes=(0, 1), seed=1))
        engine._rng = _ZeroRand()
        data = b'abc'
        self.assertEqual(engine.corrupt_bytes(data), data)

    def test_stats_track_delay_reorder_corrupt(self):
        engine = _ImpairmentEngine(NetworkImpairment(
            delay_ms=10,
            jitter_ms=0,
            dup_rate=1.0,
            reorder_rate=1.0,
            reorder_wait_ms=5,
            corrupt_rate=1.0,
            seed=11,
        ))
        decision = engine.decide()
        stats = engine.stats()
        self.assertEqual(stats['sent'], 1)
        self.assertEqual(stats['delayed'], 1)
        self.assertEqual(stats['duplicated'], 1)
        self.assertEqual(stats['reordered'], 1)
        self.assertEqual(stats['corrupted'], 1)
        self.assertGreater(decision.delay_sec, 0.0)


class LossyTransportTests(unittest.TestCase):
    """Tests for LossyTransport wrapper."""

    def test_passthrough_no_impairment(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        corr_id = lossy.send(b'test', permit)
        result = lossy.recv(timeout=0)

        self.assertEqual(result, (corr_id, b'test'))

    def test_full_loss_drops_all(self):
        inner = MockTransport()
        imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyTransport(inner, imp)

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        corr_id = lossy.send(b'test', permit)
        self.assertIsNotNone(corr_id)

        self.assertEqual(len(inner._sent), 0)
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

    def test_corruption_drop_mode(self):
        inner = MockTransport()
        imp = NetworkImpairment(
            corrupt_rate=1.0,
            corrupt_bytes=(1, 1),
            corrupt_mode='drop',
            seed=42,
        )
        lossy = LossyTransport(inner, imp)

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'\x00' * 10, permit)

        self.assertEqual(len(inner._sent), 0)

    def test_corruption_mutate_mode(self):
        inner = MockTransport()
        imp = NetworkImpairment(
            corrupt_rate=1.0,
            corrupt_bytes=(1, 1),
            corrupt_mode='mutate',
            seed=42,
        )
        lossy = LossyTransport(inner, imp)

        payload = b'\x00' * 10
        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(payload, permit)

        self.assertEqual(len(inner._sent), 1)
        self.assertNotEqual(inner._sent[0], payload)

    def test_duplication_maps_same_wrapper_id(self):
        inner = MockTransport()
        imp = NetworkImpairment(dup_rate=1.0, seed=42)
        lossy = LossyTransport(inner, send_impairment=imp,
                               recv_impairment=no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        wrapper_id = lossy.send(b'test', permit)

        self.assertEqual(len(inner._sent), 2)

        first = lossy.recv(timeout=0)
        second = lossy.recv(timeout=0)

        self.assertEqual(first[0], wrapper_id)
        self.assertEqual(second[0], wrapper_id)
        self.assertEqual(first[1], b'test')
        self.assertEqual(second[1], b'test')

    def test_send_delay_buffers_inner_send(self):
        inner = MockTransport()
        imp = NetworkImpairment(delay_ms=100, seed=42)
        lossy = LossyTransport(inner, send_impairment=imp,
                               recv_impairment=no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'test', permit)

        self.assertEqual(len(inner._sent), 0)
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

        time_provider.sleep(0.15)
        lossy.recv(timeout=0)
        self.assertEqual(len(inner._sent), 1)

    def test_send_reorder_buffers_inner_send(self):
        inner = MockTransport()
        imp = NetworkImpairment(reorder_rate=1.0, reorder_wait_ms=50, seed=42)
        lossy = LossyTransport(inner, send_impairment=imp,
                               recv_impairment=no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'test', permit)

        self.assertEqual(len(inner._sent), 0)
        time_provider.sleep(0.06)
        lossy.recv(timeout=0)
        self.assertEqual(len(inner._sent), 1)

    def test_pending_count_includes_dropped_response(self):
        inner = MockTransport()
        recv_imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyTransport(
            inner,
            send_impairment=no_impairment(),
            recv_impairment=recv_imp,
            pending_timeout_sec=0.05,
        )

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'test', permit)

        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))
        self.assertEqual(lossy.pending_count(), 1)

        time_provider.sleep(0.1)
        lossy.recv(timeout=0)
        self.assertEqual(lossy.pending_count(), 0)

    def test_drop_recv_does_not_recurse(self):
        inner = MockTransport()
        recv_imp = NetworkImpairment(loss_rate=1.0, seed=42)
        lossy = LossyTransport(inner, send_impairment=no_impairment(),
                               recv_impairment=recv_imp)

        for _ in range(5):
            permit = lossy.reserve_send()
            self.assertIsNotNone(permit)
            lossy.send(b'test', permit)

        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

    def test_stats_accessible(self):
        inner = MockTransport()
        imp = NetworkImpairment(loss_rate=0.5, seed=42)
        lossy = LossyTransport(inner, imp)

        for _ in range(10):
            permit = lossy.reserve_send()
            self.assertIsNotNone(permit)
            lossy.send(b'test', permit)

        stats = lossy.stats()
        self.assertIn('send', stats)
        self.assertIn('recv', stats)

    def test_mtu_passthrough(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        self.assertEqual(lossy.send_mtu, inner.send_mtu)
        self.assertEqual(lossy.recv_mtu, inner.recv_mtu)
        self.assertEqual(lossy.max_in_flight, inner.max_in_flight)

    def test_reserve_send_uses_inner_once(self):
        inner = MockTransport()
        imp = NetworkImpairment(dup_rate=0.0, corrupt_rate=0.0, seed=42)
        lossy = LossyTransport(inner, imp)

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        self.assertEqual(inner.reserve_calls, 1)
        lossy.send(b'test', permit)
        self.assertEqual(inner.reserve_calls, 1)

    def test_send_rejects_oversized_payload(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        with self.assertRaises(TransportError):
            lossy.send(b'a' * (inner.send_mtu + 1), permit)

    def test_release_send_releases_inner_permit(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        self.assertEqual(inner.release_calls, 0)
        lossy.release_send(permit)
        self.assertEqual(inner.release_calls, 1)

    def test_release_send_rejects_used_permit(self):
        inner = MockTransport()
        lossy = LossyTransport(inner, no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'test', permit)
        with self.assertRaises(TransportError):
            lossy.release_send(permit)

    def test_recv_duplication_queues_second_copy(self):
        inner = MockTransport()
        recv_imp = NetworkImpairment(dup_rate=1.0, seed=42)
        lossy = LossyTransport(inner, send_impairment=no_impairment(),
                               recv_impairment=recv_imp)

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        wrapper_id = lossy.send(b'test', permit)

        first = lossy.recv(timeout=0)
        second = lossy.recv(timeout=0)

        self.assertEqual(first, (wrapper_id, b'test'))
        self.assertEqual(second, (wrapper_id, b'test'))

    def test_recv_reorder_delays_response(self):
        inner = MockTransport()
        recv_imp = NetworkImpairment(
            reorder_rate=1.0,
            reorder_wait_ms=10,
            seed=42,
        )
        lossy = LossyTransport(inner, send_impairment=no_impairment(),
                               recv_impairment=recv_imp)

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        wrapper_id = lossy.send(b'test', permit)

        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))

        time_provider.sleep(0.02)
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (wrapper_id, b'test'))

    def test_close_releases_scheduled_send(self):
        inner = MockTransport()
        imp = NetworkImpairment(delay_ms=10, seed=42)
        lossy = LossyTransport(inner, send_impairment=imp,
                               recv_impairment=no_impairment())

        permit = lossy.reserve_send()
        self.assertIsNotNone(permit)
        lossy.send(b'test', permit)

        self.assertEqual(inner.release_calls, 0)
        lossy.close()
        self.assertGreater(inner.release_calls, 0)
        self.assertEqual(inner.close_calls, 1)


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
        self.assertEqual(inner._responses, [])

    def test_corruption_drops_request(self):
        inner = MockServer()
        imp = NetworkImpairment(
            corrupt_rate=1.0,
            corrupt_bytes=(1, 1),
            corrupt_mode='drop',
            seed=42,
        )
        lossy = LossyServer(inner, recv_impairment=imp)

        inner.inject_request(b'\x00' * 10)
        result = lossy.recv(timeout=0)

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

    def test_send_delay_and_dup_queued(self):
        inner = MockServer()
        imp = NetworkImpairment(delay_ms=10, dup_rate=1.0, seed=42)
        lossy = LossyServer(inner, recv_impairment=no_impairment(),
                            send_impairment=imp)

        inner.inject_request(b'request')
        data, responder = lossy.recv(timeout=0)

        self.assertEqual(data, b'request')
        responder(b'response')
        self.assertEqual(inner._responses, [])

        time_provider.sleep(0.02)
        lossy.recv(timeout=0)
        self.assertEqual(inner._responses, [b'response', b'response'])

    def test_close_clears_queues_and_closes_inner(self):
        inner = MockServer()
        recv_imp = NetworkImpairment(delay_ms=10, seed=42)
        send_imp = NetworkImpairment(delay_ms=10, seed=43)
        lossy = LossyServer(inner, recv_impairment=recv_imp,
                            send_impairment=send_imp)

        inner.inject_request(b'request')
        result = lossy.recv(timeout=0)
        self.assertEqual(result, (None, None))
        self.assertGreater(len(lossy._request_queue), 0)

        time_provider.sleep(0.02)
        data, responder = lossy.recv(timeout=0)
        self.assertEqual(data, b'request')
        responder(b'response')
        self.assertGreater(len(lossy._response_queue), 0)

        lossy.close()
        self.assertEqual(len(lossy._request_queue), 0)
        self.assertEqual(len(lossy._response_queue), 0)
        self.assertEqual(inner.close_calls, 1)


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
        engine1 = _ImpairmentEngine(imp1)
        engine2 = _ImpairmentEngine(imp2)
        drops1 = [engine1.decide().drop for _ in range(10)]
        drops2 = [engine2.decide().drop for _ in range(10)]
        self.assertEqual(drops1, drops2)


if __name__ == '__main__':
    unittest.main()
