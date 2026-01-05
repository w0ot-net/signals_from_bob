# -*- coding: ascii -*-
"""Tests for nc_linux module helpers."""

from __future__ import absolute_import

import os
import sys
import tempfile
import unittest

from sfb.config import Config
from sfb.modules.nc_linux.nc_linux import _is_linux, _open_spec, _parse_spec


@unittest.skipUnless(_is_linux(), 'linux only')
class NcLinuxSpecTests(unittest.TestCase):
    def test_parse_numeric_fd(self):
        kind, value = _parse_spec('3')
        self.assertEqual(kind, 'fd')
        self.assertEqual(value, 3)

    def test_parse_path(self):
        kind, value = _parse_spec('/tmp/test.txt')
        self.assertEqual(kind, 'path')
        self.assertEqual(value, '/tmp/test.txt')

    def test_parse_host_port(self):
        kind, value = _parse_spec('1.1.1.1:443')
        self.assertEqual(kind, 'addr')
        self.assertEqual(value, ('1.1.1.1', 443))

    def test_parse_ipv6_host_port(self):
        kind, value = _parse_spec('[::1]:443')
        self.assertEqual(kind, 'addr')
        self.assertEqual(value, ('::1', 443))

    def test_open_path_creates(self):
        config = Config()
        temp_dir = tempfile.mkdtemp()
        path = os.path.join(temp_dir, 'nc_linux_test.txt')
        bound = None
        try:
            bound = _open_spec(path, config)
            self.assertTrue(os.path.exists(path))
        finally:
            if bound is not None:
                bound.close()
            try:
                os.unlink(path)
            except Exception:
                pass
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass


if __name__ == '__main__':
    unittest.main()
