# -*- coding: ascii -*-
"""
Packet header encoding and decoding.

Packet Header (38 bytes):
    0       1       2       3
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |      seq      |      ack      |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    4                               35
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |                          sack (256 bits)                      |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    36      37
   +-+-+-+-+-+-+-+-+
   | flags |  rsvd |
   +-+-+-+-+-+-+-+-+

All multi-byte fields are big-endian.
"""

from __future__ import absolute_import

import struct

from .constants import (
    PACKET_HEADER_SIZE,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
    FLAG_WANTS_POLL,
    SEQ_MAX,
    SEQ_HALF,
    SACK_MAX,
    SACK_BITS,
)


class PacketHeader(object):
    """
    Packet header with seq, ack, sack, and flags.

    Attributes:
        seq: Sequence number of this packet (0-65535)
        ack: Next expected sequence number from peer (0-65535)
        sack: Bitmap of 256 packets received beyond ack (bit 0 = ack + 1)
        flags: Packet flags (SYN, ACK, KEEPALIVE, HAS_SEGMENTS, WANTS_POLL)
    """

    __slots__ = ('seq', 'ack', 'sack', 'flags')

    # Struct format: big-endian, 2 unsigned shorts, 4 unsigned 64-bit, 2 unsigned bytes
    _STRUCT = struct.Struct('>HHQQQQBB')
    _VALID_FLAGS = (
        FLAG_SYN | FLAG_ACK | FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS | FLAG_WANTS_POLL
    )
    _WORD_MASK = 0xFFFFFFFFFFFFFFFF

    def __init__(self, seq=0, ack=0, sack=0, flags=0):
        self.seq = seq & SEQ_MAX
        self.ack = ack & SEQ_MAX
        self.sack = sack & SACK_MAX
        self.flags = self._validate_flags(flags)

    @property
    def syn(self):
        """True if SYN flag is set."""
        return bool(self.flags & FLAG_SYN)

    @syn.setter
    def syn(self, value):
        if value:
            self.flags |= FLAG_SYN
        else:
            self.flags &= ~FLAG_SYN

    @property
    def ack_flag(self):
        """True if ACK flag is set (handshake only)."""
        return bool(self.flags & FLAG_ACK)

    @ack_flag.setter
    def ack_flag(self, value):
        if value:
            self.flags |= FLAG_ACK
        else:
            self.flags &= ~FLAG_ACK

    @property
    def keepalive_flag(self):
        """True if KEEPALIVE flag is set."""
        return bool(self.flags & FLAG_KEEPALIVE)

    @keepalive_flag.setter
    def keepalive_flag(self, value):
        if value:
            self.flags |= FLAG_KEEPALIVE
        else:
            self.flags &= ~FLAG_KEEPALIVE

    @property
    def has_segments_flag(self):
        """True if HAS_SEGMENTS flag is set."""
        return bool(self.flags & FLAG_HAS_SEGMENTS)

    @has_segments_flag.setter
    def has_segments_flag(self, value):
        if value:
            self.flags |= FLAG_HAS_SEGMENTS
        else:
            self.flags &= ~FLAG_HAS_SEGMENTS

    @property
    def wants_poll_flag(self):
        """True if WANTS_POLL flag is set."""
        return bool(self.flags & FLAG_WANTS_POLL)

    @wants_poll_flag.setter
    def wants_poll_flag(self, value):
        if value:
            self.flags |= FLAG_WANTS_POLL
        else:
            self.flags &= ~FLAG_WANTS_POLL

    def encode(self):
        """
        Encode header to bytes.

        Returns:
            bytes: 38-byte encoded header
        """
        word0, word1, word2, word3 = self._sack_to_words(self.sack)
        return self._STRUCT.pack(
            self.seq, self.ack, word0, word1, word2, word3, self.flags, 0
        )

    @classmethod
    def decode(cls, data):
        """
        Decode header from bytes.

        Args:
            data: bytes or buffer containing at least 38 bytes

        Returns:
            PacketHeader: Decoded header

        Raises:
            ValueError: If data is too short
        """
        if len(data) < PACKET_HEADER_SIZE:
            raise ValueError(
                'Packet header requires %d bytes, got %d' %
                (PACKET_HEADER_SIZE, len(data))
            )
        seq, ack, word0, word1, word2, word3, flags, reserved = cls._STRUCT.unpack(
            data[:PACKET_HEADER_SIZE]
        )
        if reserved != 0:
            raise ValueError('Packet header reserved byte must be 0')
        flags = cls._validate_flags(flags)
        sack = cls._sack_from_words(word0, word1, word2, word3)
        return cls(seq=seq, ack=ack, sack=sack, flags=flags)

    def sack_has(self, offset):
        """
        Check if a SACK bit is set.

        Args:
            offset: Offset from ack (1-256)

        Returns:
            bool: True if packet at ack+offset was received
        """
        if offset < 1 or offset > SACK_BITS:
            return False
        return bool(self.sack & (1 << (offset - 1)))

    def sack_set(self, offset):
        """
        Set a SACK bit.

        Args:
            offset: Offset from ack (1-256)
        """
        if 1 <= offset <= SACK_BITS:
            self.sack |= (1 << (offset - 1))

    def sack_clear(self, offset):
        """
        Clear a SACK bit.

        Args:
            offset: Offset from ack (1-256)
        """
        if 1 <= offset <= SACK_BITS:
            self.sack &= ~(1 << (offset - 1))

    def __repr__(self):
        flags_str = []
        if self.syn:
            flags_str.append('SYN')
        if self.ack_flag:
            flags_str.append('ACK')
        if self.has_segments_flag:
            flags_str.append('HAS_SEGMENTS')
        if self.wants_poll_flag:
            flags_str.append('WANTS_POLL')
        if self.keepalive_flag:
            flags_str.append('KEEPALIVE')
        flags_repr = '|'.join(flags_str) if flags_str else '0'
        return 'PacketHeader(seq=%d, ack=%d, sack=0x%064x, flags=%s)' % (
            self.seq, self.ack, self.sack, flags_repr
        )

    @classmethod
    def _sack_to_words(cls, sack):
        sack = sack & SACK_MAX
        return (
            (sack >> 192) & cls._WORD_MASK,
            (sack >> 128) & cls._WORD_MASK,
            (sack >> 64) & cls._WORD_MASK,
            sack & cls._WORD_MASK,
        )

    @classmethod
    def _sack_from_words(cls, word0, word1, word2, word3):
        return (
            (word0 << 192) |
            (word1 << 128) |
            (word2 << 64) |
            word3
        ) & SACK_MAX

    @classmethod
    def _validate_flags(cls, flags):
        masked = flags & 0xFF
        if masked & ~cls._VALID_FLAGS:
            raise ValueError('Invalid flags: 0x%02x' % masked)
        return masked


def seq_lt(a, b):
    """
    Compare sequence numbers with wraparound.

    Returns True if a < b in sequence space.

    Args:
        a: First sequence number
        b: Second sequence number

    Returns:
        bool: True if a is before b in sequence space
    """
    diff = (b - a) & SEQ_MAX
    return diff < SEQ_HALF and a != b


def seq_le(a, b):
    """
    Compare sequence numbers with wraparound.

    Returns True if a <= b in sequence space.

    Args:
        a: First sequence number
        b: Second sequence number

    Returns:
        bool: True if a is before or equal to b in sequence space
    """
    return a == b or seq_lt(a, b)


def seq_gt(a, b):
    """
    Compare sequence numbers with wraparound.

    Returns True if a > b in sequence space.

    Args:
        a: First sequence number
        b: Second sequence number

    Returns:
        bool: True if a is after b in sequence space
    """
    return seq_lt(b, a)


def seq_ge(a, b):
    """
    Compare sequence numbers with wraparound.

    Returns True if a >= b in sequence space.

    Args:
        a: First sequence number
        b: Second sequence number

    Returns:
        bool: True if a is after or equal to b in sequence space
    """
    return a == b or seq_gt(a, b)


def seq_diff(a, b):
    """
    Calculate signed difference between sequence numbers.

    Positive if a > b, negative if a < b.

    Args:
        a: First sequence number
        b: Second sequence number

    Returns:
        int: Signed difference (a - b) in sequence space
    """
    diff = (a - b) & SEQ_MAX
    if diff >= SEQ_HALF:
        return diff - (SEQ_MAX + 1)
    return diff
