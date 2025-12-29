# -*- coding: ascii -*-
"""
Channel manager - multiplexes channels over the tunnel.

Responsibilities:
- Maintains registry of active channels
- Allocates channel IDs (odd=Alice, even=Bob)
- Routes incoming segments to channels
- Collects outgoing segments from channels
- Handles channel lifecycle (open/close)
"""

from __future__ import absolute_import

import logging
import threading

logger = logging.getLogger(__name__)

from .channel import (
    Channel,
    ChannelError,
    CHANNEL_CONTROL,
    STATE_INIT,
    STATE_OPENING,
    STATE_OPEN,
    STATE_CLOSING,
    STATE_CLOSED,
    is_alice_channel,
    is_bob_channel,
)
from .control_channel import ControlChannel
from .channel_control_messages import ch_open, ch_open_ok, ch_close, ch_close_ok
from ..logging_util import log_event
from ..config import Config
from ..protocol import Segment, SEGMENT_HEADER_SIZE


class ChannelManager(object):
    """
    Manages channels for a tunnel endpoint.

    Handles channel allocation, routing, and lifecycle.
    """

    def __init__(self, is_alice, config):
        """
        Create a channel manager.

        Args:
            is_alice: True if this is Alice (client), False for Bob (server)
            config: Config instance with channel settings
        """
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')

        self._is_alice = is_alice
        self._config = config
        self._channels = {}  # channel_id -> Channel
        self._lock = threading.Lock()

        # Channel ID allocation
        # Alice uses odd IDs (1, 3, 5...), Bob uses even (2, 4, 6...)
        self._next_channel_id = 1 if is_alice else 2

        # Control channel (always exists)
        self._control = ControlChannel(
            max_send_buf=config.channel_max_send_buf,
            read_chunk_size=config.channel_control_read_chunk,
            write_backoff_initial=config.channel_write_backoff_initial,
            write_backoff_max=config.channel_write_backoff_max,
        )
        self._control._set_state(STATE_OPEN)
        self._channels[CHANNEL_CONTROL] = self._control

        # Round-robin index for segment packing (see CHANNEL_MANAGER.md)
        self._rr_index = 0

    @property
    def control(self):
        """The control channel (channel 0)."""
        return self._control

    def has_pending_data(self):
        """Return True if any channel has queued send data."""
        with self._lock:
            return any(ch._has_send_data() for ch in self._channels.values())

    def open_channel(self):
        """
        Open a new channel.

        Channels are generic bidirectional byte streams. Application-specific
        negotiation (like SOCKS connect) should happen after the channel opens.

        Returns:
            Channel: The new channel (in OPENING state)

        Raises:
            ChannelError: if allocation fails
        """
        with self._lock:
            channel_id = self._allocate_id()
            channel = Channel(
                channel_id,
                max_send_buf=self._config.channel_max_send_buf,
                write_backoff_initial=self._config.channel_write_backoff_initial,
                write_backoff_max=self._config.channel_write_backoff_max,
            )
            channel._close_callback = self._on_channel_close
            channel._set_state(STATE_OPENING)
            self._channels[channel_id] = channel

        # Send OPEN control message
        self._control.send_message(ch_open(channel_id))
        log_event(
            logger,
            logging.DEBUG,
            'channel.open',
            'Channel open requested',
            {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
        )

        return channel

    def get_channel(self, channel_id):
        """
        Get a channel by ID.

        Args:
            channel_id: Channel ID

        Returns:
            Channel or None
        """
        with self._lock:
            return self._channels.get(channel_id)

    def close_channel(self, channel_id):
        """
        Close a channel.

        Args:
            channel_id: Channel ID to close
        """
        with self._lock:
            channel = self._channels.get(channel_id)

        if channel is not None:
            if channel._close_callback is None:
                channel._close_callback = self._on_channel_close
            channel.close()

    def _on_channel_close(self, channel_id):
        """Callback invoked when channel.close() is called."""
        self._control.send_message(ch_close(channel_id))
        log_event(
            logger,
            logging.DEBUG,
            'channel.close',
            'Channel close requested',
            {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
        )

    def deliver_segment(self, segment):
        """
        Deliver an incoming segment to the appropriate channel.

        Args:
            segment: Segment instance
        """
        channel_id = segment.channel

        with self._lock:
            channel = self._channels.get(channel_id)

        if channel is None:
            logger.warning('Segment for unknown channel %d, ignoring', channel_id)
            return

        channel._deliver(segment.data)

    def handle_control_message(self, msg):
        """
        Handle a control message from the peer.

        Args:
            msg: Parsed JSON control message (dict)
        """
        cmd = msg.get('c')
        if not cmd:
            return

        if cmd == 'open':
            self._handle_open(msg)
        elif cmd == 'open_ok':
            self._handle_open_ok(msg)
        elif cmd == 'open_fail':
            self._handle_open_fail(msg)
        elif cmd == 'close':
            self._handle_close(msg)
        elif cmd == 'close_ok':
            self._handle_close_ok(msg)
        # ping/pong and other messages handled by tunnel

    def collect_segments(self, max_payload, keepalive_data=None):
        """
        Collect segments from channels for transmission.

        Implements the packing rules from CHANNEL_MANAGER.md:
        1. Channel 0 priority (non-keepalive data first)
        2. Keepalive suppression (no ping/pong if other data exists)
        3. Primary channel fill (round-robin selection)
        4. Round-robin fill for remaining space

        Args:
            max_payload: Max total segment bytes to collect
            keepalive_data: Optional keepalive bytes (ping/pong) to include
                           only if no other data is being sent

        Returns:
            list: List of Segment instances
        """
        segments = []
        remaining = max_payload

        # Step 1: Channel 0 non-keepalive data first (priority)
        if remaining > SEGMENT_HEADER_SIZE:
            ctrl_data = self._control._take_send_data(
                remaining - SEGMENT_HEADER_SIZE
            )
            if ctrl_data:
                segments.append(Segment(CHANNEL_CONTROL, ctrl_data))
                remaining -= SEGMENT_HEADER_SIZE + len(ctrl_data)

        # Step 2: Get snapshot of data channels and filter to those with data
        with self._lock:
            channel_snapshot = {
                cid: ch for cid, ch in self._channels.items()
                if cid != CHANNEL_CONTROL
            }
            rr_index = self._rr_index

        active_channels = [
            cid for cid, ch in channel_snapshot.items()
            if ch._has_send_data()
        ]

        # Step 3: Primary channel fill (round-robin selection)
        if active_channels and remaining > SEGMENT_HEADER_SIZE:
            # Select primary channel via round-robin
            if rr_index >= len(active_channels):
                rr_index = 0
            primary_idx = rr_index
            primary_id = active_channels[primary_idx]

            channel = channel_snapshot.get(primary_id)
            if channel is not None:
                data = channel._take_send_data(remaining - SEGMENT_HEADER_SIZE)
                if data:
                    segments.append(Segment(primary_id, data))
                    remaining -= SEGMENT_HEADER_SIZE + len(data)

            # Advance round-robin pointer
            with self._lock:
                self._rr_index = (primary_idx + 1) % len(active_channels)

            # Step 4: Fill remaining space from other channels (round-robin)
            if remaining > SEGMENT_HEADER_SIZE:
                # Start from the channel after primary
                for i in range(len(active_channels)):
                    if remaining <= SEGMENT_HEADER_SIZE:
                        break

                    idx = (primary_idx + 1 + i) % len(active_channels)
                    cid = active_channels[idx]
                    if cid == primary_id:
                        continue

                    channel = channel_snapshot.get(cid)
                    if channel is None or not channel._has_send_data():
                        continue

                    data = channel._take_send_data(
                        remaining - SEGMENT_HEADER_SIZE
                    )
                    if data:
                        segments.append(Segment(cid, data))
                        remaining -= SEGMENT_HEADER_SIZE + len(data)

        # Step 5: Keepalive suppression - only add keepalive if no other data
        if not segments and keepalive_data and remaining > SEGMENT_HEADER_SIZE:
            if len(keepalive_data) <= remaining - SEGMENT_HEADER_SIZE:
                segments.append(Segment(CHANNEL_CONTROL, keepalive_data))

        if segments or keepalive_data:
            payload_bytes = 0
            for seg in segments:
                payload_bytes += len(seg.data)
            log_event(
                logger,
                logging.DEBUG,
                'channel.pack',
                'Packed segments',
                {
                    'seg_count': len(segments),
                    'payload_bytes': payload_bytes,
                    'max_payload': max_payload,
                    'keepalive': bool(keepalive_data),
                    'side': 'alice' if self._is_alice else 'bob',
                },
            )

        return segments

    def _allocate_id(self):
        """
        Allocate next channel ID. Must hold lock.

        Channel IDs are 8-bit (1-255 for data channels). Alice uses odd IDs,
        Bob uses even. IDs wrap around and skip any that are still in use.

        Raises:
            ChannelError: if no IDs are available
        """
        # Maximum channel ID is 255 (8-bit)
        # Alice: 1, 3, 5, ..., 255 (128 possible)
        # Bob: 2, 4, 6, ..., 254 (127 possible)
        start_id = self._next_channel_id
        channel_id = start_id

        while True:
            if channel_id not in self._channels:
                # Found an available ID
                self._next_channel_id = channel_id + 2
                # Handle wraparound
                if self._next_channel_id > 255:
                    self._next_channel_id = 1 if self._is_alice else 2
                return channel_id

            # Try next ID
            channel_id += 2
            if channel_id > 255:
                channel_id = 1 if self._is_alice else 2

            # Check if we've wrapped around completely
            if channel_id == start_id:
                raise ChannelError('no_ids', 'No channel IDs available')

    def _handle_open(self, msg):
        """Handle OPEN request from peer."""
        channel_id = msg.get('ch')

        if channel_id is None:
            return

        # Validate channel ID ownership
        if self._is_alice and is_alice_channel(channel_id):
            # Alice received open for Alice's channel - invalid
            return
        if not self._is_alice and is_bob_channel(channel_id):
            # Bob received open for Bob's channel - invalid
            return

        # Auto-accept: channels are generic pipes, application layer
        # handles any additional negotiation after channel is open
        with self._lock:
            if channel_id in self._channels:
                return
            channel = Channel(
                channel_id,
                max_send_buf=self._config.channel_max_send_buf,
                write_backoff_initial=self._config.channel_write_backoff_initial,
                write_backoff_max=self._config.channel_write_backoff_max,
            )
            channel._close_callback = self._on_channel_close
            channel._set_state(STATE_OPEN)
            self._channels[channel_id] = channel

        self._control.send_message(ch_open_ok(channel_id))
        log_event(
            logger,
            logging.DEBUG,
            'channel.open_in',
            'Channel open received',
            {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
        )

    def _handle_open_ok(self, msg):
        """Handle OPEN_OK response."""
        channel_id = msg.get('ch')
        if channel_id is None:
            return

        with self._lock:
            channel = self._channels.get(channel_id)

        if channel is None:
            return

        if channel.state == STATE_OPENING:
            channel._set_state(STATE_OPEN)
            log_event(
                logger,
                logging.DEBUG,
                'channel.open_ok',
                'Channel open ok',
                {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
            )

    def _handle_open_fail(self, msg):
        """Handle OPEN_FAIL response."""
        channel_id = msg.get('ch')
        reason = msg.get('reason', 'unknown')

        if channel_id is None:
            return

        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                return
            if channel.state == STATE_OPENING:
                channel._set_state(STATE_CLOSED, error=reason)
                self._channels.pop(channel_id, None)
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.open_fail',
                    'Channel open failed',
                    {'ch': channel_id, 'reason': reason, 'side': 'alice' if self._is_alice else 'bob'},
                )

    def _handle_close(self, msg):
        """Handle CLOSE request from peer."""
        channel_id = msg.get('ch')
        if channel_id is None:
            return

        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                return
            channel._set_state(STATE_CLOSED)
            self._channels.pop(channel_id, None)

        # Send CLOSE_OK (outside lock to avoid blocking)
        self._control.send_message(ch_close_ok(channel_id))
        log_event(
            logger,
            logging.DEBUG,
            'channel.close_in',
            'Channel close received',
            {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
        )

    def _handle_close_ok(self, msg):
        """Handle CLOSE_OK response."""
        channel_id = msg.get('ch')
        if channel_id is None:
            return

        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                return
            if channel.state == STATE_CLOSING:
                channel._set_state(STATE_CLOSED)
                self._channels.pop(channel_id, None)
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.close_ok',
                    'Channel close ok',
                    {'ch': channel_id, 'side': 'alice' if self._is_alice else 'bob'},
                )
