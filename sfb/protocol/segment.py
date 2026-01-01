# -*- coding: ascii -*-
"""
Segment encoding and decoding.

Segment Header (3 bytes):
    0       1       2
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |channel|     len       |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Payload immediately follows header.

Channel IDs:
    0: Control channel (always open, reserved)
    Odd (1, 3, 5...): Dynamically opened by Alice
    Even (2, 4, 6...): Dynamically opened by Bob
"""

from __future__ import absolute_import

import struct

from .constants import (
    SEGMENT_HEADER_SIZE,
    CHANNEL_CONTROL,
    DEFAULT_MAX_PACKET_SIZE,
    PACKET_HEADER_SIZE,
)
from ..compat import to_bytes


class Segment(object):
    """
    A segment containing channel data.

    Attributes:
        channel: Channel ID (0-255)
        data: Payload bytes
    """

    __slots__ = ('channel', 'data')

    # Struct format: big-endian, 1 unsigned byte, 1 unsigned short
    _STRUCT = struct.Struct('>BH')

    # Maximum segment payload size
    DEFAULT_MAX_PAYLOAD = (
        DEFAULT_MAX_PACKET_SIZE - PACKET_HEADER_SIZE - SEGMENT_HEADER_SIZE
    )

    def __init__(self, channel, data):
        """
        Create a segment.

        Args:
            channel: Channel ID (0-255)
            data: Payload bytes
        """
        if not 0 <= channel <= 255:
            raise ValueError('Channel must be 0-255, got %d' % channel)
        if len(data) > 0xFFFF:
            raise ValueError('Segment data too long: %d bytes' % len(data))
        self.channel = channel
        self.data = _coerce_bytes(data)

    @property
    def is_control(self):
        """True if this is a control channel segment."""
        return self.channel == CHANNEL_CONTROL

    def encoded_size(self):
        """
        Get the total encoded size of this segment.

        Returns:
            int: Header size + payload size
        """
        return SEGMENT_HEADER_SIZE + len(self.data)

    def encode(self):
        """
        Encode segment to bytes.

        Returns:
            bytes: Encoded segment (header + payload)
        """
        header = self._STRUCT.pack(self.channel, len(self.data))
        return header + self.data

    @classmethod
    def decode(cls, data):
        """
        Decode one segment from bytes.

        Args:
            data: bytes containing at least one segment

        Returns:
            tuple: (Segment, remaining_bytes)

        Raises:
            ValueError: If data is malformed
        """
        if len(data) < SEGMENT_HEADER_SIZE:
            raise ValueError(
                'Segment header requires %d bytes, got %d' %
                (SEGMENT_HEADER_SIZE, len(data))
            )

        channel, length = cls._STRUCT.unpack(data[:SEGMENT_HEADER_SIZE])
        total_size = SEGMENT_HEADER_SIZE + length

        if len(data) < total_size:
            raise ValueError(
                'Segment payload incomplete: need %d bytes, got %d' %
                (total_size, len(data))
            )

        payload = data[SEGMENT_HEADER_SIZE:total_size]
        remaining = data[total_size:]

        return cls(channel, payload), remaining

    @classmethod
    def decode_all(cls, data):
        """
        Decode all segments from bytes.

        Args:
            data: bytes containing zero or more segments

        Returns:
            list: List of Segment objects

        Raises:
            ValueError: If data is malformed
        """
        segments = []
        while data:
            segment, data = cls.decode(data)
            segments.append(segment)
        return segments

    def __repr__(self):
        return 'Segment(channel=%d, len=%d)' % (self.channel, len(self.data))

    def __eq__(self, other):
        if not isinstance(other, Segment):
            return False
        return self.channel == other.channel and self.data == other.data

    def __ne__(self, other):
        return not self.__eq__(other)


def pack_segments(segments, max_size):
    """
    Pack segments into bytes, respecting size limit.

    Control channel (0) segments are packed first.

    Args:
        segments: Iterable of Segment objects
        max_size: Maximum total bytes to pack

    Returns:
        tuple: (packed_bytes, remaining_segments)
    """
    # Sort: control channel first
    control = []
    data = []
    for seg in segments:
        if seg.is_control:
            control.append(seg)
        else:
            data.append(seg)

    ordered = control + data

    packed = b''
    remaining = []
    for seg in ordered:
        encoded = seg.encode()
        if len(packed) + len(encoded) <= max_size:
            packed += encoded
        else:
            remaining.append(seg)

    return packed, remaining


def is_alice_channel(channel):
    """
    Check if channel ID is Alice-opened (odd).

    Args:
        channel: Channel ID

    Returns:
        bool: True if odd (Alice-opened)
    """
    return channel != CHANNEL_CONTROL and channel % 2 == 1


def _coerce_bytes(data):
    return to_bytes(data)


def is_bob_channel(channel):
    """
    Check if channel ID is Bob-opened (even, non-zero).

    Args:
        channel: Channel ID

    Returns:
        bool: True if even and non-zero (Bob-opened)
    """
    return channel != CHANNEL_CONTROL and channel % 2 == 0
