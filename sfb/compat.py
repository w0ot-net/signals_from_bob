# -*- coding: ascii -*-
"""
Small compatibility helpers for Python 2/3.
"""

from __future__ import absolute_import


try:
    text_type = unicode
except NameError:
    text_type = str


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
