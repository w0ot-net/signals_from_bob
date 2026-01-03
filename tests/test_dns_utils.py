# -*- coding: ascii -*-
"""Tests for dns_utils."""

from __future__ import absolute_import

import unittest

try:
    from io import StringIO
except ImportError:
    from StringIO import StringIO

from sfb.transport.dns import dns_utils


class _DummyResult(object):
    def __init__(self, stdout):
        self.stdout = stdout


class DnsUtilsTests(unittest.TestCase):
    def _patch_attr(self, obj, name, value):
        sentinel = object()
        original = getattr(obj, name, sentinel)
        setattr(obj, name, value)
        if original is sentinel:
            self.addCleanup(delattr, obj, name)
        else:
            self.addCleanup(setattr, obj, name, original)

    def _ensure_subprocess_error(self):
        if not hasattr(dns_utils.subprocess, 'SubprocessError'):
            class DummySubprocessError(Exception):
                pass
            self._patch_attr(
                dns_utils.subprocess,
                'SubprocessError',
                DummySubprocessError,
            )

    def _ensure_timeout_expired(self):
        if not hasattr(dns_utils.subprocess, 'TimeoutExpired'):
            class DummyTimeoutExpired(Exception):
                pass
            self._patch_attr(
                dns_utils.subprocess,
                'TimeoutExpired',
                DummyTimeoutExpired,
            )

    def test_load_system_resolvers_windows(self):
        self._patch_attr(dns_utils.os, 'name', 'nt')
        self._patch_attr(
            dns_utils,
            '_load_windows_resolvers',
            lambda: [('1.2.3.4', 53)],
        )
        self._patch_attr(
            dns_utils,
            '_load_unix_resolvers',
            lambda: [('5.6.7.8', 53)],
        )
        self.assertEqual(
            dns_utils.load_system_resolvers(),
            [('1.2.3.4', 53)],
        )

    def test_load_system_resolvers_unix(self):
        self._patch_attr(dns_utils.os, 'name', 'posix')
        self._patch_attr(
            dns_utils,
            '_load_windows_resolvers',
            lambda: [('1.2.3.4', 53)],
        )
        self._patch_attr(
            dns_utils,
            '_load_unix_resolvers',
            lambda: [('5.6.7.8', 53)],
        )
        self.assertEqual(
            dns_utils.load_system_resolvers(),
            [('5.6.7.8', 53)],
        )

    def test_load_unix_resolvers_parses_unique(self):
        resolv_conf = u"""
# comment
nameserver 8.8.8.8
nameserver 8.8.8.8
search example.com
nameserver 1.1.1.1
nameserver
nameserver 9.9.9.9 # trailing
"""

        def fake_open(path, mode='r', *args, **kwargs):
            self.assertEqual(path, '/etc/resolv.conf')
            return StringIO(resolv_conf)

        self._patch_attr(dns_utils, 'open', fake_open)
        self.assertEqual(
            dns_utils._load_unix_resolvers(),
            [('8.8.8.8', 53), ('1.1.1.1', 53), ('9.9.9.9', 53)],
        )

    def test_load_unix_resolvers_missing_file(self):
        def fake_open(*args, **kwargs):
            raise IOError('missing')

        self._patch_attr(dns_utils, 'open', fake_open)
        self.assertEqual(dns_utils._load_unix_resolvers(), [])

    def test_load_unix_resolvers_missing_file_oserror(self):
        def fake_open(*args, **kwargs):
            raise OSError('missing')

        self._patch_attr(dns_utils, 'open', fake_open)
        self.assertEqual(dns_utils._load_unix_resolvers(), [])

    def test_load_unix_resolvers_handles_whitespace_and_comments(self):
        resolv_conf = u"""
\t# comment
  nameserver\t8.8.4.4
Nameserver 9.9.9.9
nameserver 1.2.3.4#inline
"""

        def fake_open(path, mode='r', *args, **kwargs):
            self.assertEqual(path, '/etc/resolv.conf')
            return StringIO(resolv_conf)

        self._patch_attr(dns_utils, 'open', fake_open)
        self.assertEqual(
            dns_utils._load_unix_resolvers(),
            [('8.8.4.4', 53), ('9.9.9.9', 53), ('1.2.3.4', 53)],
        )

    def test_load_windows_resolvers_parses_output(self):
        self._ensure_subprocess_error()
        output = (
            'Server:  UnKnown\n'
            'Address:  10.0.0.243\n'
            '\n'
            'Non-authoritative answer:\n'
        )

        def fake_run(args, **kwargs):
            self.assertEqual(args, ['nslookup', 'google.com'])
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(
            dns_utils._load_windows_resolvers(),
            [('10.0.0.243', 53)],
        )

    def test_load_windows_resolvers_parse_failure(self):
        self._ensure_subprocess_error()
        output = 'no server here\n'

        def fake_run(*args, **kwargs):
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_run_error(self):
        self._ensure_subprocess_error()

        def fake_run(*args, **kwargs):
            raise OSError('boom')

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_subprocess_error(self):
        self._ensure_subprocess_error()

        def fake_run(*args, **kwargs):
            raise dns_utils.subprocess.SubprocessError('boom')

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_missing_address_line(self):
        self._ensure_subprocess_error()
        output = 'Server:  UnKnown\n'

        def fake_run(*args, **kwargs):
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_address_no_ipv4(self):
        self._ensure_subprocess_error()
        output = (
            'Server:  UnKnown\n'
            'Address:  ::1\n'
        )

        def fake_run(*args, **kwargs):
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_addresses_label(self):
        self._ensure_subprocess_error()
        output = (
            'Server:  UnKnown\n'
            'Addresses:  10.0.0.1\n'
            '          10.0.0.2\n'
        )

        def fake_run(*args, **kwargs):
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(
            dns_utils._load_windows_resolvers(),
            [('10.0.0.1', 53)],
        )

    def test_load_windows_resolvers_blank_line_before_address(self):
        self._ensure_subprocess_error()
        output = (
            'Server:  UnKnown\n'
            '\n'
            'Address:  10.0.0.243\n'
        )

        def fake_run(*args, **kwargs):
            return _DummyResult(output)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(
            dns_utils._load_windows_resolvers(),
            [('10.0.0.243', 53)],
        )

    def test_load_windows_resolvers_timeout_expired(self):
        self._ensure_subprocess_error()
        self._ensure_timeout_expired()

        def fake_run(*args, **kwargs):
            raise dns_utils.subprocess.TimeoutExpired('nslookup', 5)

        self._patch_attr(dns_utils.subprocess, 'run', fake_run)
        self.assertEqual(dns_utils._load_windows_resolvers(), [])

    def test_load_windows_resolvers_popen_fallback(self):
        self._ensure_subprocess_error()
        self._patch_attr(dns_utils.subprocess, 'run', None)
        output = (
            'Server:  UnKnown\n'
            'Address:  10.0.0.243\n'
        )

        class DummyProc(object):
            def __init__(self, stdout):
                self._stdout = stdout

            def communicate(self, *args, **kwargs):
                return self._stdout, ''

        def fake_popen(args, **kwargs):
            self.assertEqual(args, ['nslookup', 'google.com'])
            return DummyProc(output.encode('ascii'))

        self._patch_attr(dns_utils.subprocess, 'Popen', fake_popen)
        self.assertEqual(
            dns_utils._load_windows_resolvers(),
            [('10.0.0.243', 53)],
        )


if __name__ == '__main__':
    unittest.main()
