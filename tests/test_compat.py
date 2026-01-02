# -*- coding: ascii -*-
from __future__ import absolute_import

import array
import unittest

from sfb.compat import PY2, buffer_view, require_bytes_like, to_bytes


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


class CompatTests(unittest.TestCase):
    def test_to_bytes_accepts_bytearray_and_memoryview(self):
        data = b'hello'
        self.assertEqual(to_bytes(bytearray(data)), data)
        view = memoryview(bytearray(data))
        self.assertEqual(to_bytes(view), data)

    def test_to_bytes_rejects_text(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, to_bytes, text)

    def test_require_bytes_like_itemsize_py3(self):
        if PY2:
            return
        data = array.array('H', [1, 2, 3])
        view = memoryview(data)
        self.assertRaises(TypeError, require_bytes_like, view)

    def test_buffer_view_itemsize_py3(self):
        if PY2:
            return
        data = array.array('H', [1, 2, 3])
        self.assertRaises(TypeError, buffer_view, data)


if __name__ == '__main__':
    unittest.main()
