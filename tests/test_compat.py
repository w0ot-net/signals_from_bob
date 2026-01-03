# -*- coding: ascii -*-
from __future__ import absolute_import

import array
import unittest

from sfb.compat import (
    PY2,
    array_frombytes,
    buffer_view,
    byte_at,
    require_bytes_like,
    require_bytes_like_or_bytearray,
    to_bytes,
    to_native_str,
)


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


class CompatTests(unittest.TestCase):
    def test_array_frombytes_appends_data(self):
        data = array.array('B', [1, 2])
        array_frombytes(data, b'\x03\x04')
        self.assertEqual(data.tolist(), [1, 2, 3, 4])

    def test_to_bytes_accepts_bytearray_and_memoryview(self):
        data = b'hello'
        self.assertEqual(to_bytes(bytearray(data)), data)
        view = memoryview(bytearray(data))
        self.assertEqual(to_bytes(view), data)

    def test_to_bytes_accepts_bytes(self):
        data = b'hello'
        self.assertEqual(to_bytes(data), data)

    def test_to_bytes_rejects_text(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, to_bytes, text)

    def test_require_bytes_like_accepts_bytes_and_bytearray(self):
        data = b'hello'
        result = require_bytes_like(data)
        self.assertEqual(result, data)
        buf = bytearray(data)
        result = require_bytes_like(buf)
        if PY2:
            self.assertIsInstance(result, bytes)
            self.assertEqual(result, data)
        else:
            self.assertIsInstance(result, memoryview)
            self.assertEqual(result.tobytes(), data)

    def test_require_bytes_like_rejects_text_and_other(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, require_bytes_like, text)
        self.assertRaises(TypeError, require_bytes_like, object())

    def test_require_bytes_like_or_bytearray_accepts_bytearray(self):
        data = bytearray(b'hi')
        result = require_bytes_like_or_bytearray(data)
        if PY2:
            self.assertIs(result, data)
            self.assertEqual(result, bytearray(b'hi'))
        else:
            if isinstance(result, memoryview):
                self.assertIs(result.obj, data)
                self.assertEqual(result.tobytes(), b'hi')
            else:
                self.assertIs(result, data)
                self.assertEqual(bytes(result), b'hi')
            self.assertFalse(isinstance(result, bytes))

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

    def test_buffer_view_length_and_content(self):
        data = b'abcdef'
        view = buffer_view(data, length=3)
        if PY2:
            self.assertEqual(str(view), b'abc')
            self.assertEqual(len(view), 3)
        else:
            self.assertEqual(view.tobytes(), b'abc')
            self.assertEqual(len(view), 3)

    def test_buffer_view_rejects_text(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, buffer_view, text)

    def test_byte_at_returns_int(self):
        data = b'\x00\x10\xff'
        self.assertEqual(byte_at(data, 0), 0)
        self.assertEqual(byte_at(data, 1), 16)
        self.assertEqual(byte_at(bytearray(data), 2), 255)
        self.assertEqual(byte_at(memoryview(data), 2), 255)

    def test_to_native_str_text_and_bytes(self):
        text = _text_value('hello')
        result = to_native_str(text)
        if PY2:
            self.assertIsInstance(result, bytes)
            self.assertEqual(result, b'hello')
        else:
            self.assertIsInstance(result, str)
            self.assertEqual(result, 'hello')
        result = to_native_str(b'hello')
        if PY2:
            self.assertIsInstance(result, bytes)
            self.assertEqual(result, b'hello')
        else:
            self.assertIsInstance(result, str)
            self.assertEqual(result, 'hello')

    def test_to_native_str_replacement_py3(self):
        if PY2:
            return
        result = to_native_str(b'\xff')
        self.assertEqual(result, u'\ufffd')


if __name__ == '__main__':
    unittest.main()
