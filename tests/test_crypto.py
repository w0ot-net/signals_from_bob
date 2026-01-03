# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest
from array import array

from sfb.crypto import (RC4, XOR, Plain, CIPHER_MODES, _derive_rc4_key,
                        _require_key, _rc4_crypt)
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

    def test_plain_rejects_non_bytes_like_encrypt(self):
        self.assertRaises(TypeError, Plain().encrypt, 1)
        self.assertRaises(TypeError, Plain().encrypt, object())

    def test_plain_accepts_seq_direction(self):
        data = b'hi'
        cipher = Plain()
        self.assertEqual(cipher.encrypt(data), cipher.encrypt(data, seq=1, direction=1))
        self.assertEqual(cipher.decrypt(data), cipher.decrypt(data, seq=1, direction=1))

    def test_plain_bytes_like_returns_bytes(self):
        data = bytearray(b'abc')
        view = memoryview(data)
        result = Plain().encrypt(view)
        self.assertEqual(result, b'abc')
        self.assertIsInstance(result, bytes)

    def test_plain_bytearray_returns_bytes(self):
        data = bytearray(b'abc')
        result = Plain().encrypt(data)
        self.assertEqual(result, b'abc')
        self.assertIsInstance(result, bytes)

    def test_plain_buffer_returns_bytes(self):
        if not PY2:
            return
        buf = buffer(b'abc')
        result = Plain().encrypt(buf)
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

    def test_xor_accepts_seq_direction(self):
        key = b'key'
        data = b'hello'
        cipher = XOR(key)
        self.assertEqual(cipher.encrypt(data), cipher.encrypt(data, seq=1, direction=1))
        self.assertEqual(cipher.decrypt(data), cipher.decrypt(data, seq=1, direction=1))

    def test_rc4_roundtrip(self):
        key = b'secret'
        data = b'hello world'
        enc = RC4(key).encrypt(data, seq=1, direction=0)
        dec = RC4(key).decrypt(enc, seq=1, direction=0)
        self.assertEqual(dec, data)

    def test_rc4_deterministic_per_packet(self):
        key = b'secret'
        data = b'hello world'
        enc1 = RC4(key).encrypt(data, seq=10, direction=0)
        enc2 = RC4(key).encrypt(data, seq=10, direction=0)
        self.assertEqual(enc1, enc2)

    def test_rc4_direction_separation(self):
        key = b'secret'
        data = b'hello world'
        enc_a = RC4(key).encrypt(data, seq=5, direction=0)
        enc_b = RC4(key).encrypt(data, seq=5, direction=1)
        self.assertNotEqual(enc_a, enc_b)

    def test_rc4_seq_changes_output(self):
        key = b'secret'
        data = b'hello world' * 4
        cipher = RC4(key)
        enc_a = cipher.encrypt(data, seq=1, direction=0)
        enc_b = cipher.encrypt(data, seq=2, direction=0)
        self.assertNotEqual(enc_a, enc_b)

    def test_rc4_vector(self):
        key = b'k'
        data = b'hello'
        expected = b'\xcb\x0b\x24\x86\xf1'
        enc = RC4(key).encrypt(data, seq=1, direction=0)
        self.assertEqual(enc, expected)

    def test_rc4_requires_seq_direction(self):
        key = b'secret'
        data = b'hello'
        cipher = RC4(key)
        self.assertRaises(ValueError, cipher.encrypt, data)
        self.assertRaises(ValueError, cipher.encrypt, data, seq=1)
        self.assertRaises(ValueError, cipher.encrypt, data, direction=0)

    def test_rc4_decrypt_requires_seq_direction(self):
        key = b'secret'
        data = b'hello'
        cipher = RC4(key)
        self.assertRaises(ValueError, cipher.decrypt, data)
        self.assertRaises(ValueError, cipher.decrypt, data, seq=1)
        self.assertRaises(ValueError, cipher.decrypt, data, direction=0)

    def test_rc4_rejects_invalid_seq_direction(self):
        cipher = RC4(b'k')
        data = b'hi'
        self.assertRaises(TypeError, cipher.encrypt, data, seq='1', direction=0)
        self.assertRaises(TypeError, cipher.encrypt, data, seq=1, direction='0')
        self.assertRaises(ValueError, cipher.encrypt, data, seq=-1, direction=0)
        self.assertRaises(ValueError, cipher.encrypt, data, seq=0x10000, direction=0)
        self.assertRaises(ValueError, cipher.encrypt, data, seq=1, direction=-1)
        self.assertRaises(ValueError, cipher.encrypt, data, seq=1, direction=2)

    def test_rejects_empty_key(self):
        self.assertRaises(ValueError, XOR, b'')
        self.assertRaises(ValueError, RC4, b'')

    def test_rejects_text_key(self):
        text = _text_value('key')
        self.assertRaises(TypeError, XOR, text)
        self.assertRaises(TypeError, RC4, text)

    def test_require_key_rejects_non_bytes_like(self):
        self.assertRaises(TypeError, _require_key, 1)
        self.assertRaises(TypeError, _require_key, object())

    def test_rejects_text_data(self):
        text = _text_value('data')
        self.assertRaises(TypeError, XOR(b'k').encrypt, text)
        self.assertRaises(TypeError, RC4(b'k').encrypt, text, seq=1, direction=0)

    def test_rejects_text_data_decrypt(self):
        text = _text_value('data')
        self.assertRaises(TypeError, XOR(b'k').decrypt, text)
        self.assertRaises(TypeError, RC4(b'k').decrypt, text, seq=1, direction=0)
        self.assertRaises(TypeError, Plain().decrypt, text)

    def test_rejects_non_bytes_like_data(self):
        self.assertRaises(TypeError, XOR(b'k').encrypt, 1)
        self.assertRaises(TypeError, RC4(b'k').encrypt, 1, seq=1, direction=0)
        self.assertRaises(TypeError, XOR(b'k').encrypt, object())
        self.assertRaises(TypeError, RC4(b'k').encrypt, object(), seq=1, direction=0)

    def test_rejects_non_bytes_like_data_decrypt(self):
        self.assertRaises(TypeError, XOR(b'k').decrypt, 1)
        self.assertRaises(TypeError, RC4(b'k').decrypt, 1, seq=1, direction=0)
        self.assertRaises(TypeError, Plain().decrypt, 1)

    def test_cipher_modes_mapping(self):
        self.assertEqual(CIPHER_MODES['none'], Plain)
        self.assertEqual(CIPHER_MODES['xor'], XOR)
        self.assertEqual(CIPHER_MODES['rc4'], RC4)

    def test_rc4_derive_key_contents(self):
        base_key = _require_key(b'k')
        derived = _derive_rc4_key(base_key, 1, 0)
        expected = base_key + bytearray(b'\x00\x01\x00')
        self.assertEqual(derived, expected)

    def test_rc4_derive_key_validation(self):
        base_key = _require_key(b'k')
        self.assertRaises(TypeError, _derive_rc4_key, base_key, '1', 0)
        self.assertRaises(TypeError, _derive_rc4_key, base_key, 1, '0')
        self.assertRaises(ValueError, _derive_rc4_key, base_key, -1, 0)
        self.assertRaises(ValueError, _derive_rc4_key, base_key, 0x10000, 0)
        self.assertRaises(ValueError, _derive_rc4_key, base_key, 1, -1)
        self.assertRaises(ValueError, _derive_rc4_key, base_key, 1, 2)

    def test_rc4_rejects_empty_key(self):
        self.assertRaises(ValueError, _rc4_crypt, bytearray(), b'hi')

    def test_rc4_crypt_rejects_non_bytes_like_data(self):
        key = bytearray(b'k')
        text = _text_value('data')
        self.assertRaises(TypeError, _rc4_crypt, key, text)
        self.assertRaises(TypeError, _rc4_crypt, key, 1)

    def test_rc4_seq_boundaries(self):
        base_key = _require_key(b'k')
        derived = _derive_rc4_key(base_key, 0, 0)
        self.assertEqual(derived[-3:], b'\x00\x00\x00')
        derived = _derive_rc4_key(base_key, 0xFFFF, 1)
        self.assertEqual(derived[-3:], b'\xff\xff\x01')

    def test_xor_accepts_memoryview_data(self):
        if PY2:
            return
        data = memoryview(bytearray(b'hello'))
        enc = XOR(b'k').encrypt(data)
        self.assertEqual(len(enc), len(data))
        self.assertIsInstance(enc, bytes)

    def test_rejects_non_byte_itemsize_views(self):
        if PY2:
            return
        data = memoryview(array('H', [1, 2, 3]))
        self.assertRaises(TypeError, XOR(b'k').encrypt, data)
        self.assertRaises(TypeError, RC4(b'k').encrypt, data, seq=1, direction=0)
        self.assertRaises(TypeError, Plain().encrypt, data)

    def test_xor_vector(self):
        key = b'\x01\x02'
        data = b'\x10\x20\x30'
        enc = XOR(key).encrypt(data)
        self.assertEqual(enc, b'\x11\x22\x31')

    def test_rc4_accepts_memoryview_data(self):
        if PY2:
            return
        data = memoryview(bytearray(b'hello'))
        enc = RC4(b'k').encrypt(data, seq=1, direction=0)
        self.assertEqual(len(enc), len(data))
        self.assertIsInstance(enc, bytes)

    def test_plain_ignores_psk(self):
        text = _text_value('ignored')
        data = b'hi'
        self.assertEqual(Plain(text).encrypt(data), data)
        self.assertEqual(Plain(object()).decrypt(data), data)

    def test_empty_payloads(self):
        empty = b''
        self.assertEqual(XOR(b'k').encrypt(empty), empty)
        self.assertEqual(RC4(b'k').encrypt(empty, seq=1, direction=0), empty)


if __name__ == '__main__':
    unittest.main()
