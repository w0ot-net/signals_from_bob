# -*- coding: ascii -*-
"""
Small compatibility helpers for Python 2/3.
"""

from __future__ import absolute_import


import sys

try:
    text_type = unicode
except NameError:
    text_type = str

PY2 = sys.version_info[0] == 2


def require_bytes(data):
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
