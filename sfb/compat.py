# -*- coding: ascii -*-
"""
Small compatibility helpers for Python 2/3.
"""

from __future__ import absolute_import


import sys

PY2 = sys.version_info[0] == 2

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
        try:
            return bytes(bytearray(data))
        except TypeError:
            raise TypeError('Expected bytes-like object')

    def to_bytes(data):
        """
        Coerce bytes-like input to bytes, rejecting text.
        """
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
            return data
        if isinstance(data, bytearray):
            return memoryview(data)
        try:
            return memoryview(data)
        except TypeError:
            raise TypeError('Expected bytes-like object')

    def to_bytes(data):
        """
        Coerce bytes-like input to bytes, rejecting text.
        """
        if isinstance(data, text_type):
            raise TypeError('Expected bytes, got text')
        if isinstance(data, bytes):
            return data
        if isinstance(data, memoryview):
            return data.tobytes()
        if isinstance(data, bytearray):
            return bytes(data)
        try:
            return bytes(bytearray(data))
        except TypeError:
            raise TypeError('Expected bytes-like object')


def require_bytes(data):
    """
    Require bytes input, rejecting text.
    """
    return to_bytes(data)


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
