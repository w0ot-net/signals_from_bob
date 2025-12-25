# -*- coding: ascii -*-
"""
Protocol constants.

All sizes in bytes unless otherwise noted.
"""

# Packet limits
DEFAULT_MAX_PACKET_SIZE = 1450
DEFAULT_MTU = 100  # Until MTU negotiation completes

# Header sizes
PACKET_HEADER_SIZE = 8
SEGMENT_HEADER_SIZE = 3

# Header field offsets (big-endian)
SEQ_OFFSET = 0
SEQ_SIZE = 2
ACK_OFFSET = 2
ACK_SIZE = 2
SACK_OFFSET = 4
SACK_SIZE = 2
FLAGS_OFFSET = 6
FLAGS_SIZE = 1
RESERVED_OFFSET = 7
RESERVED_SIZE = 1

# Flags (bit positions)
FLAG_SYN = 0x01
FLAG_ACK = 0x02

# Sequence number space
SEQ_MAX = 0xFFFF
SEQ_HALF = 0x8000  # For wraparound comparison

# SACK bitmap
SACK_BITS = 16

# Windowing
MAX_IN_FLIGHT = 16  # Also max value, matches SACK bitmap size
DEFAULT_MAX_IN_FLIGHT = 8

# Channel IDs
CHANNEL_CONTROL = 0
# Odd channels (1, 3, 5...): Opened by Alice
# Even channels (2, 4, 6...): Opened by Bob

# Timeouts (milliseconds)
DEFAULT_RTO_MS = 1000
MIN_RTO_MS = 500
MAX_RTO_MS = 10000

# Connection timeout
ALICE_TIMEOUT_PACKETS = 30  # Packets sent without response
BOB_TIMEOUT_SECONDS = 60    # Seconds without poll
