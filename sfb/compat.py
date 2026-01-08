# -*- coding: ascii -*-
"""
Small compatibility helpers for Python 2/3.
"""

from __future__ import absolute_import


import sys

PY2 = sys.version_info[0] == 2

if hasattr(memoryview, 'tobytes'):
    _VIEW_TO_BYTES = memoryview.tobytes
else:
    _VIEW_TO_BYTES = memoryview.tostring

def bytes_from_view(view):
    """
    Return bytes from a memoryview-like object.
    """
    return _VIEW_TO_BYTES(view)

if PY2:
    text_type = unicode
    integer_types = (int, long)
    import Queue as queue
    def array_frombytes(arr, data):
        """
        Load bytes into an array (Python 2 uses fromstring).
        """
        return arr.fromstring(data)

    def require_bytes_like(data):
        """
        Require bytes-like input, rejecting text.
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        raise TypeError('Expected bytes-like object')

    def require_bytes_like_or_bytearray(data):
        """
        Require bytes-like input, accepting bytearray without copying.
        """
        if isinstance(data, bytearray):
            return data
        return require_bytes_like(data)

    def buffer_view(data, length=None):
        """
        Return a buffer view limited to length when provided.
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        try:
            if length is None:
                return buffer(data)
            return buffer(data, 0, length)
        except TypeError:
            if isinstance(data, memoryview):
                data = bytes_from_view(data)
            else:
                data = require_bytes_like(data)
            if length is None:
                return buffer(data)
            return buffer(data, 0, length)

    def to_bytes(data):
        """
        Coerce bytes-like input to bytes, rejecting text.

        Use only at bytes-only boundaries (concatenation, encoding/decoding,
        immutable queues).
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, memoryview):
            return bytes_from_view(data)
        try:
            if isinstance(data, buffer):
                return str(data)
        except NameError:
            pass
        return require_bytes_like(data)
else:
    text_type = str
    integer_types = (int,)
    import queue
    def array_frombytes(arr, data):
        """
        Load bytes into an array (Python 3 uses frombytes).
        """
        return arr.frombytes(data)

    def require_bytes_like(data):
        """
        Require bytes-like input, rejecting text.
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        if isinstance(data, bytes):
            return data
        if isinstance(data, memoryview):
            if data.itemsize != 1:
                raise TypeError('Expected bytes-like object')
            return data
        if isinstance(data, bytearray):
            return memoryview(data)
        try:
            view = memoryview(data)
        except TypeError:
            raise TypeError('Expected bytes-like object')
        if view.itemsize != 1:
            raise TypeError('Expected bytes-like object')
        return view

    def require_bytes_like_or_bytearray(data):
        """
        Require bytes-like input, accepting bytearray without copying.
        """
        return require_bytes_like(data)

    def buffer_view(data, length=None):
        """
        Return a memoryview limited to length when provided.
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        view = data if isinstance(data, memoryview) else memoryview(data)
        if view.itemsize != 1:
            raise TypeError('Expected bytes-like object')
        if length is not None and len(view) != length:
            view = view[:length]
        return view

    def to_bytes(data):
        """
        Coerce bytes-like input to bytes, rejecting text.

        Use only at bytes-only boundaries (concatenation, encoding/decoding,
        immutable queues).
        """
        data = require_bytes_like(data)
        return data if isinstance(data, bytes) else bytes_from_view(data)


def byte_at(data, offset):
    """
    Get byte value at offset (Python 2/3 compatible).
    """
    b = data[offset]
    return b if isinstance(b, int) else ord(b)


def to_native_str(value):
    """
    Convert a value to the native str type for the runtime.
    """
    if isinstance(value, text_type):
        return value.encode('utf-8') if PY2 else value
    if isinstance(value, bytes):
        return value if PY2 else value.decode('utf-8', 'replace')
    try:
        return str(value)
    except Exception:
        return repr(value)
