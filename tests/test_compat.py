# -*- coding: ascii -*-
from __future__ import absolute_import

import array
import unittest

from sfb.compat import (
    PY2,
    array_frombytes,
    buffer_view,
    byte_at,
    integer_types,
    queue,
    require_bytes_like,
    require_bytes_like_or_bytearray,
    text_type,
    to_bytes,
    to_native_str,
)


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


class CompatTests(unittest.TestCase):
    def test_text_and_integer_types(self):
        text = _text_value('hello')
        self.assertTrue(isinstance(text, text_type))
        self.assertTrue(isinstance(1, integer_types))
        if PY2:
            self.assertTrue(isinstance(long(1), integer_types))

    def test_queue_alias(self):
        self.assertTrue(hasattr(queue, 'Queue'))

    def test_array_frombytes_appends_data(self):
        data = array.array('B', [1, 2])
        array_frombytes(data, b'\x03\x04')
        self.assertEqual(data.tolist(), [1, 2, 3, 4])

    def test_array_frombytes_accepts_bytearray(self):
        data = array.array('B')
        array_frombytes(data, bytearray(b'\x01\x02'))
        self.assertEqual(data.tolist(), [1, 2])

    def test_array_frombytes_accepts_memoryview(self):
        data = array.array('B')
        array_frombytes(data, memoryview(b'\x01\x02'))
        self.assertEqual(data.tolist(), [1, 2])

    def test_array_frombytes_accepts_non_byte_array(self):
        data = array.array('H', [1, 2])
        payload = data.tostring() if PY2 else data.tobytes()
        other = array.array('H')
        array_frombytes(other, payload)
        self.assertEqual(other.tolist(), [1, 2])

    def test_to_bytes_accepts_bytearray_and_memoryview(self):
        data = b'hello'
        self.assertEqual(to_bytes(bytearray(data)), data)
        view = memoryview(bytearray(data))
        self.assertEqual(to_bytes(view), data)

    def test_to_bytes_accepts_bytes(self):
        data = b'hello'
        self.assertEqual(to_bytes(data), data)

    def test_to_bytes_accepts_buffer_py2(self):
        if not PY2:
            return
        data = b'hello'
        buf = buffer(data)
        self.assertEqual(to_bytes(buf), data)

    def test_to_bytes_accepts_memoryview_py2(self):
        if not PY2:
            return
        data = b'hello'
        view = memoryview(data)
        self.assertEqual(to_bytes(view), data)

    def test_to_bytes_rejects_text(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, to_bytes, text)

    def test_to_bytes_rejects_non_bytes_like(self):
        self.assertRaises(TypeError, to_bytes, object())

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

    def test_require_bytes_like_accepts_memoryview_py3(self):
        if PY2:
            return
        data = bytearray(b'hello')
        view = memoryview(data)
        result = require_bytes_like(view)
        self.assertIs(result, view)
        self.assertEqual(result.tobytes(), b'hello')

    def test_require_bytes_like_accepts_array_py3(self):
        if PY2:
            return
        data = array.array('B', [1, 2, 3])
        result = require_bytes_like(data)
        self.assertIsInstance(result, memoryview)
        self.assertEqual(result.tobytes(), b'\x01\x02\x03')

    def test_require_bytes_like_rejects_text_and_other(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, require_bytes_like, text)
        self.assertRaises(TypeError, require_bytes_like, object())

    def test_require_bytes_like_rejects_memoryview_py2(self):
        if not PY2:
            return
        self.assertRaises(TypeError, require_bytes_like, memoryview(b'hi'))
        self.assertRaises(TypeError, require_bytes_like, buffer(b'hi'))

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

    def test_require_bytes_like_or_bytearray_accepts_bytes(self):
        data = b'hi'
        result = require_bytes_like_or_bytearray(data)
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, data)

    def test_require_bytes_like_or_bytearray_accepts_memoryview_py3(self):
        if PY2:
            return
        data = bytearray(b'hi')
        view = memoryview(data)
        result = require_bytes_like_or_bytearray(view)
        self.assertIs(result, view)
        self.assertEqual(result.tobytes(), b'hi')

    def test_require_bytes_like_or_bytearray_accepts_array_py3(self):
        if PY2:
            return
        data = array.array('B', [1, 2])
        result = require_bytes_like_or_bytearray(data)
        self.assertIsInstance(result, memoryview)
        self.assertEqual(result.tobytes(), b'\x01\x02')

    def test_require_bytes_like_or_bytearray_rejects_text(self):
        text = _text_value('hi')
        self.assertRaises(TypeError, require_bytes_like_or_bytearray, text)

    def test_require_bytes_like_itemsize_py3(self):
        if PY2:
            return
        data = array.array('H', [1, 2, 3])
        view = memoryview(data)
        self.assertRaises(TypeError, require_bytes_like, view)

    def test_require_bytes_like_rejects_array_py3(self):
        if PY2:
            return
        data = array.array('H', [1, 2, 3])
        self.assertRaises(TypeError, require_bytes_like, data)

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

    def test_buffer_view_full_length_default(self):
        data = b'abcdef'
        view = buffer_view(data)
        if PY2:
            self.assertEqual(str(view), data)
            self.assertEqual(len(view), len(data))
        else:
            self.assertEqual(view.tobytes(), data)
            self.assertEqual(len(view), len(data))

    def test_buffer_view_length_equals_input_py3(self):
        if PY2:
            return
        data = b'abcdef'
        view = buffer_view(data, length=len(data))
        self.assertEqual(view.tobytes(), data)
        self.assertEqual(len(view), len(data))

    def test_buffer_view_length_longer_py3(self):
        if PY2:
            return
        data = b'abc'
        view = buffer_view(data, length=10)
        self.assertEqual(view.tobytes(), data)
        self.assertEqual(len(view), len(data))

    def test_buffer_view_accepts_memoryview_py2(self):
        if not PY2:
            return
        data = memoryview(b'abcdef')
        view = buffer_view(data, length=4)
        self.assertEqual(str(view), b'abcd')
        self.assertEqual(len(view), 4)

    def test_buffer_view_accepts_memoryview_py3(self):
        if PY2:
            return
        data = b'abcdef'
        view = buffer_view(memoryview(data), length=4)
        self.assertEqual(view.tobytes(), b'abcd')
        self.assertEqual(len(view), 4)

    def test_buffer_view_rejects_text(self):
        text = _text_value('hello')
        self.assertRaises(TypeError, buffer_view, text)

    def test_byte_at_returns_int(self):
        data = b'\x00\x10\xff'
        self.assertEqual(byte_at(data, 0), 0)
        self.assertEqual(byte_at(data, 1), 16)
        self.assertEqual(byte_at(bytearray(data), 2), 255)
        self.assertEqual(byte_at(memoryview(data), 2), 255)

    def test_byte_at_negative_index(self):
        data = b'\x00\x10\xff'
        self.assertEqual(byte_at(data, -1), 255)
        self.assertEqual(byte_at(bytearray(data), -2), 16)
        self.assertEqual(byte_at(memoryview(data), -3), 0)

    def test_byte_at_out_of_range(self):
        data = b'\x00'
        self.assertRaises(IndexError, byte_at, data, 1)
        self.assertRaises(IndexError, byte_at, data, -2)

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

    def test_to_native_str_non_text_uses_str(self):
        value = 123
        result = to_native_str(value)
        if PY2:
            self.assertIsInstance(result, bytes)
            self.assertEqual(result, b'123')
        else:
            self.assertIsInstance(result, str)
            self.assertEqual(result, '123')

    def test_to_native_str_fallback_repr(self):
        class _BadStr(object):
            def __str__(self):
                raise Exception('boom')

            def __repr__(self):
                return 'BadStr()'

        result = to_native_str(_BadStr())
        if PY2:
            self.assertEqual(result, b'BadStr()')
        else:
            self.assertEqual(result, 'BadStr()')

    def test_to_native_str_replacement_py3(self):
        if PY2:
            return
        result = to_native_str(b'\xff')
        self.assertEqual(result, u'\ufffd')


if __name__ == '__main__':
    unittest.main()
