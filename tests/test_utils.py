# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.utils import (
    HostPortError,
    build_host_port_error_map,
    parse_host_port,
    parse_host_port_or_raise,
)


class HostPortUtilsTests(unittest.TestCase):
    def test_parse_host_port_basic(self):
        host, port = parse_host_port('127.0.0.1:53')
        self.assertEqual(host, '127.0.0.1')
        self.assertEqual(port, 53)

    def test_parse_host_port_default_port(self):
        host, port = parse_host_port('1.2.3.4', default_port=5353)
        self.assertEqual(host, '1.2.3.4')
        self.assertEqual(port, 5353)

    def test_parse_host_port_missing_port(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port('1.2.3.4')
        self.assertEqual(ctx.exception.code, 'missing_port')

    def test_parse_host_port_missing_host(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port(':443')
        self.assertEqual(ctx.exception.code, 'missing_host')

    def test_parse_host_port_invalid_port(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port('1.2.3.4:bad')
        self.assertEqual(ctx.exception.code, 'invalid_port')

    def test_parse_host_port_port_range(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port('1.2.3.4:70000')
        self.assertEqual(ctx.exception.code, 'port_range')

    def test_parse_host_port_ipv6(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port('[::1]:53')
        self.assertEqual(ctx.exception.code, 'ipv6_unsupported')

    def test_parse_host_port_not_text(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port(123)
        self.assertEqual(ctx.exception.code, 'not_text')

    def test_parse_host_port_not_ascii(self):
        with self.assertRaises(HostPortError) as ctx:
            parse_host_port(u'\u2603:53')
        self.assertEqual(ctx.exception.code, 'not_ascii')

    def test_parse_host_port_or_raise_maps(self):
        error_map = {
            'missing_port': (ValueError, 'missing port'),
        }
        with self.assertRaises(ValueError) as ctx:
            parse_host_port_or_raise('1.2.3.4', error_map)
        self.assertEqual(str(ctx.exception), 'missing port')

    def test_build_host_port_error_map_overrides(self):
        error_map = build_host_port_error_map(
            ValueError,
            base_message='base',
            overrides={'invalid_port': 'bad port'},
        )
        self.assertEqual(error_map['missing_host'][1], 'base')
        self.assertEqual(error_map['invalid_port'][1], 'bad port')


if __name__ == '__main__':
    unittest.main()
