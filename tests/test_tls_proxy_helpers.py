# -*- coding: ascii -*-
from __future__ import absolute_import

import base64
import unittest

from sfb.transport import proxy_helpers
from sfb.transport.transport_base import TransportError


class ProxyHelperTests(unittest.TestCase):
    def test_build_connect_request(self):
        request = proxy_helpers.build_connect_request('example.com:443')
        expected = (
            b'CONNECT example.com:443 HTTP/1.1\r\n'
            b'Host: example.com:443\r\n'
            b'\r\n'
        )
        self.assertEqual(request, expected)

    def test_build_connect_request_with_auth(self):
        request = proxy_helpers.build_connect_request(
            'example.com:443',
            proxy_auth='user:pass',
        )
        token = base64.b64encode(b'user:pass')
        expected = (
            b'CONNECT example.com:443 HTTP/1.1\r\n'
            b'Host: example.com:443\r\n'
            b'Proxy-Authorization: Basic ' + token + b'\r\n'
            b'\r\n'
        )
        self.assertEqual(request, expected)

    def test_parse_connect_response_valid_status(self):
        response = (
            b'HTTP/1.1 200 Connection established\r\n'
            b'Header: value\r\n'
            b'\r\n'
        )
        status, header_end = proxy_helpers.parse_connect_response(response)
        self.assertEqual(status, 200)
        self.assertEqual(header_end, response.find(b'\r\n\r\n'))

    def test_parse_connect_response_start_offset(self):
        response = (
            b'HTTP/1.1 200 Connection established\r\n'
            b'Header: value\r\n'
            b'\r\n'
        )
        header_end = response.find(b'\r\n\r\n')
        status, parsed_end = proxy_helpers.parse_connect_response(
            response,
            start_offset=header_end - 1,
        )
        self.assertEqual(status, 200)
        self.assertEqual(parsed_end, header_end)

    def test_parse_connect_response_invalid_status(self):
        response = b'HTTP/1.1 OK\r\n\r\n'
        status, header_end = proxy_helpers.parse_connect_response(response)
        self.assertIsNone(status)
        self.assertEqual(header_end, response.find(b'\r\n\r\n'))

    def test_parse_connect_response_too_large(self):
        response = b'a' * (proxy_helpers.PROXY_HEADER_LIMIT + 1)
        status, header_end = proxy_helpers.parse_connect_response(response)
        self.assertIsNone(status)
        self.assertEqual(header_end, proxy_helpers.PROXY_HEADER_TOO_LARGE)

    def test_validate_proxy_config_defaults_timeout(self):
        values = proxy_helpers.validate_proxy_config(
            'example.com:8080',
            None,
            None,
            2.5,
        )
        self.assertEqual(values['proxy_timeout'], 2.5)

    def test_validate_proxy_config_proxy_timeout_without_proxy(self):
        values = proxy_helpers.validate_proxy_config(
            None,
            None,
            1.5,
            2.5,
        )
        self.assertEqual(values['proxy_timeout'], 1.5)
        self.assertIsNone(values['proxy_addr'])
        self.assertIsNone(values['proxy_auth'])

    def test_validate_proxy_config_auth_requires_proxy(self):
        with self.assertRaises(TransportError):
            proxy_helpers.validate_proxy_config(
                None,
                'user:pass',
                None,
                1.0,
            )

    def test_validate_proxy_config_invalid_auth(self):
        with self.assertRaises(TransportError):
            proxy_helpers.validate_proxy_config(
                'example.com:8080',
                'userpass',
                None,
                1.0,
            )

    def test_validate_proxy_config_invalid_proxy(self):
        with self.assertRaises(TransportError):
            proxy_helpers.validate_proxy_config(
                'example.com',
                None,
                None,
                1.0,
            )


if __name__ == '__main__':
    unittest.main()
