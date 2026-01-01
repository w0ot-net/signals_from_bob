# -*- coding: ascii -*-
"""
Tests for logging helpers.
"""

from __future__ import absolute_import

import logging
import unittest

from sfb.logging_util import log_event


class LogEventTests(unittest.TestCase):
    def test_fields_callable_not_evaluated_when_disabled(self):
        logger = logging.getLogger('sfb.tests.log_event')
        logger.setLevel(logging.INFO)
        called = []

        def fields():
            called.append(True)
            return {'k': 'v'}

        log_event(logger, logging.DEBUG, 'test.event', 'Test', fields)
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
