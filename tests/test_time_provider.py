# -*- coding: ascii -*-
"""
Tests for time_provider.
"""

from __future__ import absolute_import

import unittest

from sfb import time_provider


class TimeProviderTests(unittest.TestCase):
    def tearDown(self):
        time_provider.reset_time_source()

    def test_now_non_decreasing_with_clamp(self):
        values = [5.0, 4.0, 4.5, 3.0, 6.0]

        def fake_time():
            return values.pop(0)

        time_provider.set_time_source(fake_time, clamp=True)
        self.assertEqual(time_provider.now(), 5.0)
        self.assertEqual(time_provider.now(), 5.0)
        self.assertEqual(time_provider.now(), 5.0)
        self.assertEqual(time_provider.now(), 5.0)
        self.assertEqual(time_provider.now(), 6.0)

    def test_reset_restores_default_source(self):
        default_state = time_provider._get_default_state()
        self.assertEqual(time_provider._get_state(), default_state)

        def fake_time():
            return 1.0

        time_provider.set_time_source(fake_time, clamp=True)
        self.assertNotEqual(time_provider._get_state(), default_state)
        time_provider.reset_time_source()
        self.assertEqual(time_provider._get_state(), default_state)


if __name__ == '__main__':
    unittest.main()
