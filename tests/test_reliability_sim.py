# -*- coding: ascii -*-
from __future__ import absolute_import

import random
import unittest

from sfb.reliability import RttEstimator, SendWindow, RecvWindow


class LossyQueue(object):
    def __init__(self, rng, loss_rate=0.0, dup_rate=0.0, max_delay_ticks=0,
                 burst_prob=0.0, burst_len_range=(1, 1)):
        self._rng = rng
        self._loss_rate = loss_rate
        self._dup_rate = dup_rate
        self._max_delay_ticks = max_delay_ticks
        self._burst_prob = burst_prob
        self._burst_len_range = burst_len_range
        self._burst_remaining = 0
        self._queue = []

    def send(self, payload, now, tick_sec, extra_delay_ticks=None):
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            return
        if self._burst_prob and self._rng.random() < self._burst_prob:
            burst_len = self._rng.randint(
                self._burst_len_range[0], self._burst_len_range[1]
            )
            self._burst_remaining = max(0, burst_len - 1)
            return
        if self._rng.random() < self._loss_rate:
            return
        delay_ticks = self._rng.randint(0, self._max_delay_ticks)
        if extra_delay_ticks:
            delay_ticks += extra_delay_ticks
        deliver_time = now + delay_ticks * tick_sec
        self._queue.append((deliver_time, payload))
        if self._rng.random() < self._dup_rate:
            delay_ticks = self._rng.randint(0, self._max_delay_ticks)
            if extra_delay_ticks:
                delay_ticks += extra_delay_ticks
            deliver_time = now + delay_ticks * tick_sec
            self._queue.append((deliver_time, payload))

    def deliver(self, now):
        ready = []
        remaining = []
        for deliver_time, payload in self._queue:
            if deliver_time <= now:
                ready.append((deliver_time, payload))
            else:
                remaining.append((deliver_time, payload))
        self._queue = remaining
        ready.sort(key=lambda item: item[0])
        return [payload for _, payload in ready]


class ReliabilitySim(object):
    def __init__(self, num_packets, max_in_flight, loss_rate,
                 dup_rate, max_delay_ticks, poll_interval, seed,
                 burst_prob=0.0, burst_len_range=(1, 1),
                 ack_delay_ticks_range=(0, 0)):
        self._rng = random.Random(seed)
        self._data = [self._make_payload(i) for i in range(num_packets)]
        self._send_index = 0
        self._send_times = {}
        self._latencies = []
        self._max_sent_in_tick = 0

        self._send_win = SendWindow(max_in_flight=max_in_flight)
        self._recv_win = RecvWindow(max_buffer=max_in_flight)
        self._rtt = RttEstimator()

        self._tick_sec = poll_interval
        self._a_to_b = LossyQueue(
            self._rng, loss_rate, dup_rate, max_delay_ticks,
            burst_prob=burst_prob, burst_len_range=burst_len_range
        )
        self._b_to_a = LossyQueue(
            self._rng, loss_rate, dup_rate, max_delay_ticks,
            burst_prob=burst_prob, burst_len_range=burst_len_range
        )
        self._ack_delay_ticks_range = ack_delay_ticks_range

    @staticmethod
    def _make_payload(index):
        return b'pkt%04d' % index

    def run(self, max_ticks):
        delivered = []
        tick = 0
        while tick < max_ticks:
            now = tick * self._tick_sec

            rto_sec = self._rtt.rto_sec
            for seq, data, _ in self._send_win.get_retransmits(
                    rto_sec, now=now
            ):
                self._send_win.mark_retransmit(seq, now=now)
                self._a_to_b.send((seq, data), now, self._tick_sec)

            sent_this_tick = 0
            while (self._send_win.can_send and
                   self._send_index < len(self._data)):
                data = self._data[self._send_index]
                seq = self._send_win.send(data, now=now)
                if data not in self._send_times:
                    self._send_times[data] = now
                self._send_index += 1
                self._a_to_b.send((seq, data), now, self._tick_sec)
                sent_this_tick += 1
            if sent_this_tick > self._max_sent_in_tick:
                self._max_sent_in_tick = sent_this_tick

            for seq, data in self._a_to_b.deliver(now):
                ready = self._recv_win.receive(seq, data)
                for _, payload in ready:
                    delivered.append(payload)
                    sent = self._send_times.get(payload)
                    if sent is not None:
                        self._latencies.append(now - sent)
                ack = self._recv_win.ack
                sack = self._recv_win.sack
                ack_delay = self._rng.randint(
                    self._ack_delay_ticks_range[0],
                    self._ack_delay_ticks_range[1]
                )
                self._b_to_a.send(
                    (ack, sack), now, self._tick_sec,
                    extra_delay_ticks=ack_delay
                )

            for ack, sack in self._b_to_a.deliver(now):
                samples, _ = self._send_win.process_ack(ack, sack, now=now)
                for sample in samples:
                    self._rtt.add_sample(sample)

            if (len(delivered) == len(self._data) and
                    self._send_win.unacked_count == 0):
                return delivered

            tick += 1

        raise AssertionError('Simulation did not complete')

    def latency_stats(self):
        if not self._latencies:
            return None
        total = sum(self._latencies)
        return (min(self._latencies),
                max(self._latencies),
                total / float(len(self._latencies)))

    @property
    def max_sent_in_tick(self):
        return self._max_sent_in_tick


class ReliabilitySimulationTests(unittest.TestCase):
    def test_heavy_loss_eventual_delivery(self):
        sim = ReliabilitySim(
            num_packets=50,
            max_in_flight=8,
            loss_rate=0.4,
            dup_rate=0.1,
            max_delay_ticks=5,
            poll_interval=0.1,
            seed=123,
        )
        delivered = sim.run(max_ticks=2000)
        expected = [sim._make_payload(i) for i in range(50)]
        self.assertEqual(delivered, expected)

    def test_burst_loss_eventual_delivery(self):
        sim = ReliabilitySim(
            num_packets=30,
            max_in_flight=8,
            loss_rate=0.1,
            dup_rate=0.1,
            max_delay_ticks=6,
            poll_interval=0.1,
            seed=321,
            burst_prob=0.08,
            burst_len_range=(3, 8),
        )
        delivered = sim.run(max_ticks=2500)
        expected = [sim._make_payload(i) for i in range(30)]
        self.assertEqual(delivered, expected)

    def test_burst_loss_with_delayed_acks(self):
        sim = ReliabilitySim(
            num_packets=25,
            max_in_flight=6,
            loss_rate=0.2,
            dup_rate=0.1,
            max_delay_ticks=5,
            poll_interval=0.1,
            seed=555,
            burst_prob=0.1,
            burst_len_range=(2, 6),
            ack_delay_ticks_range=(2, 6),
        )
        delivered = sim.run(max_ticks=2500)
        expected = [sim._make_payload(i) for i in range(25)]
        self.assertEqual(delivered, expected)

    def test_pipelined_sends_multiple_per_tick(self):
        sim = ReliabilitySim(
            num_packets=64,
            max_in_flight=16,
            loss_rate=0.0,
            dup_rate=0.0,
            max_delay_ticks=0,
            poll_interval=0.1,
            seed=77,
        )
        delivered = sim.run(max_ticks=200)
        expected = [sim._make_payload(i) for i in range(64)]
        self.assertEqual(delivered, expected)
        self.assertGreaterEqual(sim.max_sent_in_tick, 8)

    def test_extreme_loss_eventual_delivery(self):
        sim = ReliabilitySim(
            num_packets=20,
            max_in_flight=4,
            loss_rate=0.7,
            dup_rate=0.2,
            max_delay_ticks=8,
            poll_interval=0.2,
            seed=999,
        )
        delivered = sim.run(max_ticks=3000)
        expected = [sim._make_payload(i) for i in range(20)]
        self.assertEqual(delivered, expected)

    def test_latency_stats_under_low_delay(self):
        sim = ReliabilitySim(
            num_packets=10,
            max_in_flight=4,
            loss_rate=0.0,
            dup_rate=0.0,
            max_delay_ticks=2,
            poll_interval=0.05,
            seed=101,
            ack_delay_ticks_range=(3, 3),
        )
        sim.run(max_ticks=200)
        stats = sim.latency_stats()
        self.assertIsNotNone(stats)
        min_lat, max_lat, avg_lat = stats
        self.assertGreaterEqual(min_lat, 0.0)
        self.assertLessEqual(max_lat, 0.101)
        self.assertLessEqual(avg_lat, 0.08)

    def test_long_run_10000_packets(self):
        sim = ReliabilitySim(
            num_packets=10000,
            max_in_flight=16,
            loss_rate=0.0,
            dup_rate=0.0,
            max_delay_ticks=0,
            poll_interval=0.01,
            seed=2024,
        )
        delivered = sim.run(max_ticks=1000)
        expected = [sim._make_payload(i) for i in range(10000)]
        self.assertEqual(delivered, expected)
    def test_wraparound_delivery(self):
        sim = ReliabilitySim(
            num_packets=6,
            max_in_flight=4,
            loss_rate=0.0,
            dup_rate=0.0,
            max_delay_ticks=2,
            poll_interval=0.05,
            seed=42,
        )
        sim._send_win._next_seq = 0xFFFE
        sim._recv_win.set_initial_seq(0xFFFE)
        delivered = sim.run(max_ticks=200)
        expected = [sim._make_payload(i) for i in range(6)]
        self.assertEqual(delivered, expected)

    def test_randomized_seeds(self):
        seeds = [5, 11, 23, 41, 97]
        for seed in seeds:
            sim = ReliabilitySim(
                num_packets=25,
                max_in_flight=6,
                loss_rate=0.35,
                dup_rate=0.1,
                max_delay_ticks=4,
                poll_interval=0.1,
                seed=seed,
            )
            delivered = sim.run(max_ticks=2000)
            expected = [sim._make_payload(i) for i in range(25)]
            self.assertEqual(delivered, expected)


if __name__ == '__main__':
    unittest.main()
