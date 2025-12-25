# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.crypto import RC4, XOR, Plain


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


class CryptoTests(unittest.TestCase):
    def test_plain_passthrough(self):
        data = b'abc'
        self.assertEqual(Plain().encrypt(data), data)
        self.assertEqual(Plain().decrypt(data), data)

    def test_plain_rejects_text(self):
        text = _text_value('hi')
        self.assertRaises(TypeError, Plain().encrypt, text)

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
