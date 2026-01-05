# -*- coding: ascii -*-
"""Tests for sfb.utils helpers."""

from __future__ import absolute_import

import unittest

from sfb.utils import parse_host_port


class ParseHostPortTests(unittest.TestCase):
    def _assert_error(self, value, message, default_port=None):
        try:
            parse_host_port(value, default_port=default_port)
        except ValueError as exc:
            self.assertEqual(str(exc), message)
        else:
            self.fail('Expected ValueError')

    def test_parse_host_port_basic(self):
        host, port = parse_host_port(u'1.1.1.1:443')
        self.assertEqual(host, u'1.1.1.1')
        self.assertEqual(port, 443)

    def test_parse_host_port_default_port(self):
        host, port = parse_host_port(u'resolver.local', default_port=53)
        self.assertEqual(host, u'resolver.local')
        self.assertEqual(port, 53)

    def test_parse_host_port_requires_port(self):
        self._assert_error(u'example.com', 'Address must include port')

    def test_parse_host_port_missing_host(self):
        self._assert_error(u':53', 'Address host required')

    def test_parse_host_port_invalid_port(self):
        self._assert_error(u'example.com:abc', 'Address port invalid')

    def test_parse_host_port_out_of_range(self):
        self._assert_error(u'example.com:99999', 'Address port out of range')

    def test_parse_host_port_ipv6_rejected(self):
        self._assert_error(
            u'[::1]:53',
            'Address must be host:port (IPv6 unsupported)',
        )

    def test_parse_host_port_multi_colon_rejected(self):
        self._assert_error(
            u'a:b:c',
            'Address must be host:port (IPv6 unsupported)',
        )

    def test_parse_host_port_ascii_required(self):
        self._assert_error(u'\u2603:53', 'Address must be ASCII')

    def test_parse_host_port_text_required(self):
        self._assert_error(123, 'Address must be text')


if __name__ == '__main__':
    unittest.main()
