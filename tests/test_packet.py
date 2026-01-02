# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.protocol import (
    Packet,
    PacketHeader,
    Segment,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    PACKET_HEADER_SIZE,
)
from sfb.protocol.constants import SACK_OFFSET, SACK_SIZE


class PacketHeaderTests(unittest.TestCase):
    def _expected_sack_bytes(self, offset):
        bits = [0] * SACK_SIZE
        bit_index = offset - 1
        byte_index = (SACK_SIZE - 1) - (bit_index // 8)
        bit_in_byte = bit_index % 8
        bits[byte_index] = 1 << bit_in_byte
        return bytes(bytearray(bits))

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
        self.assertRaises(ValueError, PacketHeader, flags=0x08)

    def test_keepalive_flag_roundtrip(self):
        header = PacketHeader(seq=10, ack=20, sack=0, flags=FLAG_KEEPALIVE)
        data = header.encode()
        decoded = PacketHeader.decode(data)
        self.assertEqual(decoded.flags, FLAG_KEEPALIVE)

    def test_sack_wire_order_boundaries(self):
        for offset in (64, 65, 128, 129, 192, 193, 255, 256):
            sack = 1 << (offset - 1)
            header = PacketHeader(seq=0, ack=0, sack=sack, flags=0)
            data = header.encode()
            self.assertEqual(len(data), PACKET_HEADER_SIZE)
            sack_bytes = data[SACK_OFFSET:SACK_OFFSET + SACK_SIZE]
            self.assertEqual(sack_bytes, self._expected_sack_bytes(offset))

    def test_sack_masks_to_256_bits(self):
        header = PacketHeader(sack=(1 << 256) | 1)
        self.assertEqual(header.sack, 1)


class PacketTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        packet = Packet(seq=5, ack=7)
        packet.add_segment(Segment(0, b'{"cmd":"hello"}\n'))
        data = packet.encode()
        decoded = Packet.decode(data)
        self.assertEqual(decoded.seq, 5)
        self.assertEqual(decoded.ack, 7)
        self.assertEqual(len(decoded.segments), 1)
        self.assertEqual(decoded.segments[0].channel, 0)
        self.assertEqual(decoded.segments[0].data, b'{"cmd":"hello"}\n')

    def test_decode_enforces_max_size(self):
        packet = Packet(seq=1, ack=1)
        packet.add_segment(Segment(1, b'abc'))
        data = packet.encode()
        self.assertRaises(ValueError, Packet.decode, data, max_size=len(data) - 1)


if __name__ == '__main__':
    unittest.main()
