# -*- coding: ascii -*-
"""
Protocol encoding and decoding.

This module provides packet and segment structures for the tunnel protocol.

Example usage:

    from sfb.protocol import Packet, Segment

    # Create a packet with segments
    packet = Packet(seq=1, ack=0)
    packet.add_segment(Segment(0, b'{"cmd":"hello"}\\n'))

    # Encode to bytes
    data = packet.encode()

    # Decode from bytes
    packet = Packet.decode(data)
    for segment in packet.segments:
        print(segment.channel, segment.data)
"""

from __future__ import absolute_import

import logging

from ..logging_util import get_logger, log_event

from .constants import (
    DEFAULT_MAX_PACKET_SIZE,
    DEFAULT_MTU,
    PACKET_HEADER_SIZE,
    SEGMENT_HEADER_SIZE,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    SEQ_MAX,
    SACK_BITS,
    MAX_IN_FLIGHT,
    DEFAULT_MAX_IN_FLIGHT,
    CHANNEL_CONTROL,
    DEFAULT_RTO_MS,
    MIN_RTO_MS,
    MAX_RTO_MS,
    ALICE_TIMEOUT_PACKETS,
    BOB_TIMEOUT_SECONDS,
)

from .packet import (
    PacketHeader,
    seq_lt,
    seq_le,
    seq_gt,
    seq_ge,
    seq_diff,
)

from .segment import (
    Segment,
    pack_segments,
    is_alice_channel,
    is_bob_channel,
)


class Packet(object):
    """
    A complete packet with header and segments.

    Attributes:
        header: PacketHeader instance
        segments: List of Segment instances
    """

    __slots__ = ('header', 'segments')

    def __init__(self, seq=0, ack=0, sack=0, flags=0, segments=None):
        """
        Create a packet.

        Args:
            seq: Sequence number
            ack: Acknowledgment number
            sack: SACK bitmap
            flags: Packet flags
            segments: Optional list of Segment instances
        """
        self.header = PacketHeader(seq=seq, ack=ack, sack=sack, flags=flags)
        self.segments = list(segments) if segments else []

    @property
    def seq(self):
        return self.header.seq

    @seq.setter
    def seq(self, value):
        self.header.seq = value

    @property
    def ack(self):
        return self.header.ack

    @ack.setter
    def ack(self, value):
        self.header.ack = value

    @property
    def sack(self):
        return self.header.sack

    @sack.setter
    def sack(self, value):
        self.header.sack = value

    @property
    def flags(self):
        return self.header.flags

    @flags.setter
    def flags(self, value):
        self.header.flags = value

    @property
    def syn(self):
        return self.header.syn

    @syn.setter
    def syn(self, value):
        self.header.syn = value

    @property
    def ack_flag(self):
        return self.header.ack_flag

    @ack_flag.setter
    def ack_flag(self, value):
        self.header.ack_flag = value

    @property
    def keepalive_flag(self):
        return self.header.keepalive_flag

    @keepalive_flag.setter
    def keepalive_flag(self, value):
        self.header.keepalive_flag = value

    def add_segment(self, segment):
        """
        Add a segment to this packet.

        Args:
            segment: Segment instance
        """
        self.segments.append(segment)

    def encoded_size(self):
        """
        Get the total encoded size of this packet.

        Returns:
            int: Header size + all segment sizes
        """
        size = PACKET_HEADER_SIZE
        for seg in self.segments:
            size += seg.encoded_size()
        return size

    def encode(self):
        """
        Encode packet to bytes.

        Returns:
            bytes: Encoded packet
        """
        parts = [self.header.encode()]
        for seg in self.segments:
            parts.append(seg.encode())
        return b''.join(parts)

    @classmethod
    def decode(cls, data, max_size=None):
        """
        Decode packet from bytes.

        Args:
            data: bytes containing a packet
            max_size: Optional max packet size to enforce

        Returns:
            Packet: Decoded packet

        Raises:
            ValueError: If data is malformed
        """
        if max_size is not None and len(data) > max_size:
            raise ValueError(
                'Packet size %d exceeds max %d' % (len(data), max_size)
            )
        header = PacketHeader.decode(data)
        segments = Segment.decode_all(data[PACKET_HEADER_SIZE:])

        packet = cls()
        packet.header = header
        packet.segments = segments
        _log_control_segments(segments)
        return packet

    def __repr__(self):
        return 'Packet(%r, segments=%d)' % (self.header, len(self.segments))


__all__ = [
    # Constants
    'DEFAULT_MAX_PACKET_SIZE',
    'DEFAULT_MTU',
    'PACKET_HEADER_SIZE',
    'SEGMENT_HEADER_SIZE',
    'FLAG_SYN',
    'FLAG_ACK',
    'FLAG_KEEPALIVE',
    'SEQ_MAX',
    'SACK_BITS',
    'MAX_IN_FLIGHT',
    'DEFAULT_MAX_IN_FLIGHT',
    'CHANNEL_CONTROL',
    'DEFAULT_RTO_MS',
    'MIN_RTO_MS',
    'MAX_RTO_MS',
    'ALICE_TIMEOUT_PACKETS',
    'BOB_TIMEOUT_SECONDS',
    # Packet
    'PacketHeader',
    'Packet',
    'seq_lt',
    'seq_le',
    'seq_gt',
    'seq_ge',
    'seq_diff',
    # Segment
    'Segment',
    'pack_segments',
    'is_alice_channel',
    'is_bob_channel',
    'log_control_segments',
]


_LOG = get_logger(__name__)


def _log_control_segments(segments):
    for seg in segments:
        if not seg.is_control:
            continue
        data = seg.data
        if b'\n' not in data:
            continue
        lines = data.split(b'\n')
        for line in lines[:-1]:
            if not line:
                continue
            log_event(
                _LOG,
                logging.INFO,
                'protocol.control',
                'Control message line',
                lambda: {'line': line.decode('ascii', 'replace')},
            )


def log_control_segments(segments):
    _log_control_segments(segments)
