# -*- coding: ascii -*-
"""
Shared base32 helpers for transport codecs.
"""

from __future__ import absolute_import

import base64

from ..compat import bytearray_to_bytes, require_bytes_like, text_type, to_bytes


def base32_encode(data, lowercase=True):
    """Encode bytes to base32 without padding."""
    data = require_bytes_like(data)
    encoded = base64.b32encode(to_bytes(data)).rstrip(b'=')
    text = encoded.decode('ascii')
    if lowercase:
        return text.lower()
    return text.upper()


def base32_decode(text):
    """Decode base32 string to bytes (handles missing padding)."""
    if not isinstance(text, text_type):
        raise TypeError('Expected text for base32 decode')
    try:
        text.encode('ascii')
    except UnicodeError:
        raise
    pad = (8 - len(text) % 8) % 8
    padded = text.upper() + ('=' * pad)
    return base64.b32decode(padded.encode('ascii'))


def base32_decode_bytes(value):
    """Decode base32 bytes to bytes (handles missing padding)."""
    if isinstance(value, text_type):
        raise TypeError('Expected bytes for base32 decode')
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, bytearray):
        raw = bytearray_to_bytes(value)
    else:
        raw = to_bytes(value)
    pad = (8 - len(raw) % 8) % 8
    if pad:
        raw = raw + (b'=' * pad)
    return base64.b32decode(raw.upper())
