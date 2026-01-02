# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.crypto import RC4, XOR, Plain
from sfb.compat import PY2


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


def _rc4_encrypt(cipher, data):
    try:
        return cipher.encrypt(data, seq=1, direction=0)
    except TypeError:
        return cipher.encrypt(data)


class CryptoTests(unittest.TestCase):
    def test_plain_passthrough(self):
        data = b'abc'
        self.assertEqual(Plain().encrypt(data), data)
        self.assertEqual(Plain().decrypt(data), data)

    def test_plain_rejects_text(self):
        text = _text_value('hi')
        self.assertRaises(TypeError, Plain().encrypt, text)

    def test_plain_bytes_like_returns_bytes(self):
        data = bytearray(b'abc')
        view = memoryview(data)
        result = Plain().encrypt(view)
        self.assertEqual(result, b'abc')
        self.assertIsInstance(result, bytes)

    def test_accepts_bytes_like_key(self):
        key = bytearray(b'key')
        data = b'hello'
        enc = XOR(key).encrypt(data)
        self.assertEqual(len(enc), len(data))
        self.assertIsInstance(enc, bytes)
        enc = _rc4_encrypt(RC4(key), data)
        self.assertEqual(len(enc), len(data))
        self.assertIsInstance(enc, bytes)
        if not PY2:
            view = memoryview(bytearray(b'key'))
            enc = XOR(view).encrypt(data)
            self.assertEqual(len(enc), len(data))
            enc = _rc4_encrypt(RC4(view), data)
            self.assertEqual(len(enc), len(data))

    def test_xor_roundtrip(self):
        key = b'key'
        data = b'hello'
        cipher = XOR(key)
        enc = cipher.encrypt(data)
        dec = XOR(key).decrypt(enc)
        self.assertEqual(dec, data)

    def test_rc4_roundtrip(self):
        key = b'secret'
        data = b'hello world'
        enc = RC4(key).encrypt(data)
        dec = RC4(key).decrypt(enc)
        self.assertEqual(dec, data)

    def test_rejects_empty_key(self):
        self.assertRaises(ValueError, XOR, b'')
        self.assertRaises(ValueError, RC4, b'')

    def test_rejects_text_key(self):
        text = _text_value('key')
        self.assertRaises(TypeError, XOR, text)
        self.assertRaises(TypeError, RC4, text)

    def test_rejects_text_data(self):
        text = _text_value('data')
        self.assertRaises(TypeError, XOR(b'k').encrypt, text)
        self.assertRaises(TypeError, RC4(b'k').encrypt, text)


if __name__ == '__main__':
    unittest.main()
