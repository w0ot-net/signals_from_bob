# -*- coding: ascii -*-
"""
Cryptographic ciphers for packet encryption.

Modes:
    - none: No encryption (passthrough)
    - xor: Simple XOR with key
    - rc4: RC4 stream cipher

The entire packet is encrypted before transport handoff.
"""

from __future__ import absolute_import

from .compat import require_bytes_like, to_bytes

class RC4(object):
    """
    RC4 stream cipher implementation.

    RC4 generates a pseudo-random keystream that is XORed with plaintext.
    The cipher maintains state, so each instance should be used for one
    direction of a connection.
    """

    def __init__(self, psk):
        """
        Initialize RC4 with a pre-shared key.

        Args:
            psk: Pre-shared key (bytes or str)
        """
        key = _require_key(psk)
        key_len = len(key)

        # Key Scheduling Algorithm (KSA)
        self._s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + self._s[i] + key[i % key_len]) & 0xFF
            self._s[i], self._s[j] = self._s[j], self._s[i]

        self._i = 0
        self._j = 0

    def crypt(self, data):
        """
        Encrypt or decrypt data.

        RC4 is symmetric - the same operation encrypts and decrypts.

        Args:
            data: Input bytes

        Returns:
            bytes: Output bytes (same length as input)
        """
        data = require_bytes_like(data)
        s = self._s
        i = self._i
        j = self._j
        data = bytearray(data)
        out = bytearray(len(data))
        for idx, val in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            out[idx] = val ^ s[(s[i] + s[j]) & 0xFF]
        self._i = i
        self._j = j
        return bytes(out)

    encrypt = crypt
    decrypt = crypt


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

    def crypt(self, data):
        """
        Encrypt or decrypt data.

        Args:
            data: Input bytes

        Returns:
            bytes: Output bytes (same length as input)
        """
        data = require_bytes_like(data)
        key = self._key
        key_len = len(key)
        data = bytearray(data)
        out = bytearray(len(data))
        for idx, val in enumerate(data):
            out[idx] = val ^ key[idx % key_len]
        return bytes(out)

    encrypt = crypt
    decrypt = crypt


class Plain(object):
    """
    No-op cipher (passthrough).
    """

    def __init__(self, psk=None):
        """Initialize. PSK is ignored."""
        pass

    def encrypt(self, data):
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
