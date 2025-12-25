# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.protocol import (
    Packet,
    PacketHeader,
    Segment,
    FLAG_SYN,
    FLAG_ACK,
)


class PacketHeaderTests(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        header = PacketHeader(seq=1, ack=2, sack=3, flags=FLAG_SYN | FLAG_ACK)
        data = header.encode()
        decoded = PacketHeader.decode(data)
        self.assertEqual(decoded.seq, 1)
        self.assertEqual(decoded.ack, 2)
        self.assertEqual(decoded.sack, 3)
        self.assertEqual(decoded.flags, FLAG_SYN | FLAG_ACK)

    def test_decode_rejects_reserved(self):
        header = PacketHeader(seq=1, ack=2, sack=3, flags=FLAG_SYN)
        data = bytearray(header.encode())
        data[-1] = 1
        self.assertRaises(ValueError, PacketHeader.decode, bytes(data))

    def test_rejects_invalid_flags(self):
        self.assertRaises(ValueError, PacketHeader, flags=0x04)


class PacketTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        packet = Packet(seq=5, ack=7)
        packet.add_segment(Segment(0, b'{"cmd":"ping"}\n'))
        data = packet.encode()
        decoded = Packet.decode(data)
        self.assertEqual(decoded.seq, 5)
        self.assertEqual(decoded.ack, 7)
        self.assertEqual(len(decoded.segments), 1)
        self.assertEqual(decoded.segments[0].channel, 0)
        self.assertEqual(decoded.segments[0].data, b'{"cmd":"ping"}\n')

    def test_decode_enforces_max_size(self):
        packet = Packet(seq=1, ack=1)
        packet.add_segment(Segment(1, b'abc'))
        data = packet.encode()
        self.assertRaises(ValueError, Packet.decode, data, max_size=len(data) - 1)


if __name__ == '__main__':
    unittest.main()
