# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from tunnel.protocol import (
    Segment,
    pack_segments,
    is_alice_channel,
    is_bob_channel,
    CHANNEL_CONTROL,
    SEGMENT_HEADER_SIZE,
)


def _text_value(value):
    try:
        return unicode(value)
    except NameError:
        return value


class SegmentTests(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        seg = Segment(1, b'abc')
        data = seg.encode()
        decoded, remaining = Segment.decode(data)
        self.assertEqual(remaining, b'')
        self.assertEqual(decoded.channel, 1)
        self.assertEqual(decoded.data, b'abc')

    def test_decode_all(self):
        seg1 = Segment(1, b'a')
        seg2 = Segment(2, b'bb')
        data = seg1.encode() + seg2.encode()
        decoded = Segment.decode_all(data)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0].data, b'a')
        self.assertEqual(decoded[1].data, b'bb')

    def test_pack_segments_control_first(self):
        control = Segment(CHANNEL_CONTROL, b'c')
        data1 = Segment(1, b'a')
        data2 = Segment(2, b'b')
        max_size = SEGMENT_HEADER_SIZE * 2 + 2
        packed, remaining = pack_segments([data1, control, data2], max_size)
        decoded = Segment.decode_all(packed)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0].channel, CHANNEL_CONTROL)
        self.assertEqual(decoded[1].channel, 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].channel, 2)

    def test_channel_helpers(self):
        self.assertTrue(is_alice_channel(1))
        self.assertFalse(is_alice_channel(2))
        self.assertTrue(is_bob_channel(2))
        self.assertFalse(is_bob_channel(1))
        self.assertFalse(is_bob_channel(CHANNEL_CONTROL))

    def test_rejects_text_payload(self):
        text = _text_value('hi')
        self.assertRaises(TypeError, Segment, 1, text)

    def test_accepts_bytearray(self):
        data = bytearray(b'xyz')
        seg = Segment(1, data)
        self.assertEqual(seg.data, b'xyz')


if __name__ == '__main__':
    unittest.main()
