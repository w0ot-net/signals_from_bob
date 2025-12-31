# -*- coding: ascii -*-
"""Tests for transport-agnostic RateLimiter."""

from __future__ import absolute_import

import unittest

from sfb.transport.transport_base import RateLimiter


class RateLimiterTest(unittest.TestCase):
    def test_disabled(self):
        limiter = RateLimiter(0)
        self.assertTrue(limiter.can_send())
        self.assertTrue(limiter.consume())

    def test_consume_and_refill(self):
        limiter = RateLimiter(2.0, burst=2.0)
        bucket = limiter._bucket
        bucket._last_refill = 0.0

        self.assertTrue(limiter.consume(now=0.0))
        self.assertTrue(limiter.consume(now=0.0))
        self.assertFalse(limiter.consume(now=0.0))
        self.assertFalse(limiter.can_send(now=0.25))

        bucket._last_refill = 0.0
        self.assertTrue(limiter.can_send(now=1.0))
        self.assertTrue(limiter.consume(now=1.0))
        self.assertTrue(limiter.consume(now=1.0))
        self.assertFalse(limiter.can_send(now=1.25))


if __name__ == '__main__':
    unittest.main()
