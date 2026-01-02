# -*- coding: ascii -*-
"""
Cryptographic ciphers for packet encryption.

Modes:
    - none: No encryption (passthrough)
    - xor: Simple XOR with key
    - rc4: RC4 stream cipher

Only the packet body (segments) is encrypted. The header remains in the clear.
RC4 is derived per packet using (seq, direction) to keep retransmits stable.
Keystreams repeat if seq wraps under a static PSK.
"""

from __future__ import absolute_import

import struct

from .compat import integer_types, require_bytes_like, to_bytes

class RC4(object):
    """
    RC4 stream cipher implementation.

    RC4 generates a pseudo-random keystream that is XORed with plaintext.
    The cipher is stateless per packet and derives its key from the packet
    sequence number and direction.
    """

    def __init__(self, psk):
        """
        Initialize RC4 with a pre-shared key.

        Args:
            psk: Pre-shared key (bytes or str)
        """
        self._base_key = _require_key(psk)

    def encrypt(self, data, seq=None, direction=None):
        """
        Encrypt data for a specific packet.

        RC4 is symmetric - the same operation encrypts and decrypts.

        Args:
            data: Input bytes
            seq: Packet sequence number (0-65535)
            direction: 0 for Alice->Bob, 1 for Bob->Alice

        Returns:
            bytes: Output bytes (same length as input)
        """
        key = _derive_rc4_key(self._base_key, seq, direction)
        return _rc4_crypt(key, data)

    decrypt = encrypt


class XOR(object):
    """
    Simple XOR cipher with repeating key.
    """

    def __init__(self, psk):
        """
        Initialize XOR cipher with a pre-shared key.

        Args:
            psk: Pre-shared key (bytes or str)
        """
        self._key = _require_key(psk)

    def crypt(self, data, seq=None, direction=None):
        """
        Encrypt or decrypt data.

        Args:
            data: Input bytes
            seq: Packet sequence number (unused)
            direction: Packet direction (unused)

        Returns:
            bytes: Output bytes (same length as input)
        """
        data = require_bytes_like(data)
        key = self._key
        key_len = len(key)
        data = bytearray(data)
        key_index = 0
        for idx in range(len(data)):
            data[idx] ^= key[key_index]
            key_index += 1
            if key_index == key_len:
                key_index = 0
        return bytes(data)

    encrypt = crypt
    decrypt = crypt


class Plain(object):
    """
    No-op cipher (passthrough).
    """

    def __init__(self, psk=None):
        """Initialize. PSK is ignored."""
        pass

    def encrypt(self, data, seq=None, direction=None):
        """Return data unchanged."""
        return to_bytes(data)

    decrypt = encrypt


# Map mode names to cipher classes
CIPHER_MODES = {
    'none': Plain,
    'xor': XOR,
    'rc4': RC4,
}


def _require_key(psk):
    """Require a non-empty key of raw bytes."""
    key = require_bytes_like(psk)
    if not key:
        raise ValueError('Key must not be empty')
    return bytearray(key)


def _derive_rc4_key(base_key, seq, direction):
    if seq is None or direction is None:
        raise ValueError('seq and direction required for rc4')
    if not isinstance(seq, integer_types):
        raise TypeError('seq must be an integer')
    if not isinstance(direction, integer_types):
        raise TypeError('direction must be an integer')
    if seq < 0 or seq > 0xFFFF:
        raise ValueError('seq must be 0-65535')
    if direction not in (0, 1):
        raise ValueError('direction must be 0 or 1')
    nonce = struct.pack('>HB', seq, direction)
    return base_key + bytearray(nonce)


def _rc4_crypt(key, data):
    data = require_bytes_like(data)
    key_len = len(key)
    if not key_len:
        raise ValueError('Key must not be empty')

    # Key Scheduling Algorithm (KSA)
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % key_len]) & 0xFF
        s[i], s[j] = s[j], s[i]

    # Pseudo-Random Generation Algorithm (PRGA)
    i = 0
    j = 0
    out = bytearray(data)
    for idx in range(len(out)):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[idx] ^= s[(s[i] + s[j]) & 0xFF]
    return bytes(out)
