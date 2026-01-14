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

import collections
import logging
import threading

from ..compat import integer_types

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
from .channel_control_messages import (
    ch_open,
    ch_open_ok,
    ch_open_fail,
    ch_close,
    ch_close_ok,
    ch_close_err,
    ch_half_close,
)
from ..logging_util import get_logger, log_event
from ..config import Config
from ..protocol import Segment, SEGMENT_HEADER_SIZE
from .. import time_provider

logger = get_logger(__name__)

_UNKNOWN_CHANNEL_NOTIFY_INTERVAL = 1.0


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
        self._side = 'alice' if is_alice else 'bob'
        self._config = config
        self._channels = {}  # channel_id -> Channel
        self._lock = threading.Lock()
        self._active_channels = collections.OrderedDict()
        self._send_state_seq = {}
        self._data_send_event = threading.Event()
        self._pending_send_event = threading.Event()
        self._unknown_channel_last = {}
        self._id_reuse_cooldown = config.channel_id_reuse_cooldown
        self._id_reuse_until = {}
        self._channel_request_handler = None
        self._control_handlers = {
            'open': self._handle_open,
            'open_ok': self._handle_open_ok,
            'open_fail': self._handle_open_fail,
            'close': self._handle_close,
            'close_ok': self._handle_close_ok,
            'close_err': self._handle_close_err,
            'half_close': self._handle_half_close,
        }

        # Channel ID allocation
        # Alice uses odd IDs (1, 3, 5...), Bob uses even (2, 4, 6...)
        self._next_channel_id = 1 if is_alice else 2

        self._write_backoff_initial = min(0.01, config.channel_write_backoff_max)

        # Control channel (always exists)
        self._control = ControlChannel(
            max_send_buf=config.channel_max_buf,
            max_recv_buf=config.channel_max_buf,
            write_backoff_initial=self._write_backoff_initial,
            write_backoff_max=config.channel_write_backoff_max,
            send_event_callback=self._on_control_send_event,
        )
        self._control._set_state(STATE_OPEN)
        self._register_channel(self._control)

        # Drain stats for debugging throughput stalls
        self._stats_lock = threading.Lock()
        self._stats_last_log = time_provider.now()
        self._stats_interval = 1.0
        self._stats_bytes_sent = {}

    @property
    def control(self):
        """The control channel (channel 0)."""
        return self._control

    @property
    def pending_send_event(self):
        """Event set when control or data channels have queued send data."""
        return self._pending_send_event

    @property
    def data_send_event(self):
        """Event set when non-control channels have queued send data."""
        return self._data_send_event

    def set_channel_request_handler(self, handler):
        """
        Set handler for remote channel open requests.

        The handler must return True to accept the channel, False to reject.
        """
        if handler is not None and not callable(handler):
            raise TypeError('handler must be callable or None')
        self._channel_request_handler = handler

    def has_pending_data(self, mode='control_or_data'):
        """Return True if channels have queued send data."""
        if mode == 'control':
            return self._control.send_event.is_set()
        if mode == 'data':
            return self._data_send_event.is_set()
        if mode == 'control_or_data':
            return self._pending_send_event.is_set()
        raise ValueError('mode must be control_or_data, control, or data')

    def has_data_channels_pending(self):
        """Return True if any non-control channel has queued send data."""
        return self._data_send_event.is_set()

    def _update_data_send_event_locked(self):
        if self._active_channels:
            self._data_send_event.set()
        else:
            self._data_send_event.clear()
        self._update_pending_send_event_locked()

    def _update_pending_send_event_locked(self):
        if (self._control.send_event.is_set() or
                self._data_send_event.is_set()):
            self._pending_send_event.set()
        else:
            self._pending_send_event.clear()

    def _on_control_send_event(self, is_set):
        if is_set:
            self._pending_send_event.set()
            return
        if self._data_send_event.is_set():
            return
        self._pending_send_event.clear()

    def _log_ctx(self, ch=None, **extra):
        ctx = {'side': self._side}
        if ch is not None:
            ctx['ch'] = ch
        if extra:
            ctx.update(extra)
        return ctx

    def pending_send_bytes(self, include_control=True):
        """Return total queued send bytes across channels."""
        with self._lock:
            items = list(self._channels.items())
        total = 0
        for channel_id, channel in items:
            if not include_control and channel_id == CHANNEL_CONTROL:
                continue
            total += channel.send_buf_size
        return total

    def _new_channel(self, channel_id, state):
        channel = Channel(
            channel_id,
            max_send_buf=self._config.channel_max_buf,
            max_recv_buf=self._config.channel_max_buf,
            write_backoff_initial=self._write_backoff_initial,
            write_backoff_max=self._config.channel_write_backoff_max,
        )
        channel._set_state(state)
        return channel

    def _register_channel(self, channel):
        with self._lock:
            self._register_channel_locked(channel)

    def _register_channel_locked(self, channel):
        channel._close_callback = self._on_channel_close
        channel._half_close_callback = self._on_channel_half_close
        def _send_state_callback(channel_id, has_data, seq,
                                 channel_ref=channel):
            self._on_channel_send_state(
                channel_ref,
                channel_id,
                has_data,
                seq,
            )
        channel._set_send_state_callback(_send_state_callback)
        self._channels[channel.id] = channel
        self._unknown_channel_last.pop(channel.id, None)
        self._id_reuse_until.pop(channel.id, None)
        if channel.id == CHANNEL_CONTROL:
            return
        has_data, seq = channel._get_send_state()
        self._send_state_seq[channel.id] = seq
        if has_data and channel.id not in self._active_channels:
            self._active_channels[channel.id] = None
            self._update_data_send_event_locked()

    def _unregister_channel_locked(self, channel_id):
        channel = self._channels.pop(channel_id, None)
        if channel is None:
            return None
        channel._set_send_state_callback(None)
        self._send_state_seq.pop(channel_id, None)
        self._unknown_channel_last.pop(channel_id, None)
        if self._id_reuse_cooldown > 0 and self._owns_channel_id(channel_id):
            self._id_reuse_until[channel_id] = (
                time_provider.now() + self._id_reuse_cooldown
            )
        if channel_id in self._active_channels:
            del self._active_channels[channel_id]
            self._update_data_send_event_locked()
        return channel

    def _on_channel_send_state(self, channel, channel_id, has_data, seq):
        if channel_id == CHANNEL_CONTROL:
            return
        with self._lock:
            if self._channels.get(channel_id) is not channel:
                return
            last_seq = self._send_state_seq.get(channel_id, 0)
            if seq <= last_seq:
                return
            self._send_state_seq[channel_id] = seq
            if has_data:
                if channel_id not in self._active_channels:
                    self._active_channels[channel_id] = None
            else:
                if channel_id in self._active_channels:
                    del self._active_channels[channel_id]
            self._update_data_send_event_locked()

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
            channel = self._new_channel(channel_id, STATE_OPENING)
            self._register_channel_locked(channel)

        # Send OPEN control message
        self._control.send_message(ch_open(channel_id))
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger,
                logging.DEBUG,
                'channel.open',
                'Channel open requested',
                lambda: self._log_ctx(channel_id),
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

    def _on_channel_close(self, channel_id, code=None, reason=None, abort=False):
        """Callback invoked when channel.close() or channel.abort() is called."""
        if abort:
            if code is None:
                code = 'aborted'
            if reason is None:
                reason = 'Channel aborted'
            self._control.send_message(ch_close_err(channel_id, code, reason))
            if logger.isEnabledFor(logging.INFO):
                log_event(
                    logger,
                    logging.INFO,
                    'channel.abort',
                    'Channel abort requested',
                    lambda: self._log_ctx(channel_id, code=code, reason=reason),
                )
            with self._lock:
                self._unregister_channel_locked(channel_id)
            return
        self._control.send_message(ch_close(channel_id))
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger,
                logging.DEBUG,
                'channel.close',
                'Channel close requested',
                lambda: self._log_ctx(channel_id),
            )

    def _on_channel_half_close(self, channel_id):
        """Callback invoked when channel.close_write() is called."""
        if channel_id == CHANNEL_CONTROL:
            return
        self._control.send_message(ch_half_close(channel_id))
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger,
                logging.DEBUG,
                'channel.half_close_out',
                'Channel half-close requested',
                lambda: self._log_ctx(channel_id),
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
            self._handle_unknown_segment(channel_id)
            return

        try:
            channel._deliver(segment.data)
        except ChannelError as e:
            if e.code == 'recv_overflow':
                channel.abort(code=e.code, message=e.message)
            else:
                if logger.isEnabledFor(logging.WARNING):
                    log_event(
                        logger,
                        logging.WARNING,
                        'channel.deliver_error',
                        'Channel delivery error',
                        lambda: self._log_ctx(
                            channel_id,
                            code=e.code,
                            reason=e.message,
                        ),
                )

    def _handle_unknown_segment(self, channel_id):
        now = time_provider.now()
        notify = False
        with self._lock:
            last = self._unknown_channel_last.get(channel_id)
            if last is None or (now - last) >= _UNKNOWN_CHANNEL_NOTIFY_INTERVAL:
                self._unknown_channel_last[channel_id] = now
                notify = True
        if notify:
            if logger.isEnabledFor(logging.WARNING):
                log_event(
                    logger,
                    logging.WARNING,
                    'channel.unknown_segment',
                    'Segment for unknown channel',
                    lambda: self._log_ctx(channel_id),
                )
        if notify and channel_id != CHANNEL_CONTROL:
            try:
                self._control.send_message(
                    ch_close_err(channel_id, 'unknown_channel', 'Unknown channel')
                )
            except ChannelError:
                return
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.close_err_out',
                    'Channel close error requested',
                    lambda: self._log_ctx(
                        channel_id,
                        code='unknown_channel',
                        reason='Unknown channel',
                    ),
            )

    def handle_control_message(self, msg):
        """
        Handle a control message from the peer.

        Args:
            msg: Parsed JSON control message (dict)
        """
        cmd = msg.get('c')
        if not cmd:
            return

        handler = self._control_handlers.get(cmd)
        if handler is None:
            return
        handler(msg)
        # Tunnel messages handled by tunnel

    def _snapshot_active_channels(self):
        active_channels = []
        inactive_ids = []
        channel_snapshot = {}
        with self._lock:
            for cid in self._active_channels:
                channel = self._channels.get(cid)
                channel_snapshot[cid] = channel
                if channel is None:
                    inactive_ids.append(cid)
                else:
                    active_channels.append(cid)
        return active_channels, channel_snapshot, inactive_ids

    def _take_segment(self, channel_id, channel, remaining, segments):
        if channel is None or remaining <= SEGMENT_HEADER_SIZE:
            return remaining
        data = channel._take_send_data(remaining - SEGMENT_HEADER_SIZE)
        if not data:
            return remaining
        segments.append(Segment(channel_id, data))
        return remaining - SEGMENT_HEADER_SIZE - len(data)

    def _drain_channels(self, channel_ids, channel_snapshot, remaining, segments):
        for cid in channel_ids:
            if remaining <= SEGMENT_HEADER_SIZE:
                break
            channel = channel_snapshot.get(cid)
            remaining = self._take_segment(cid, channel, remaining, segments)
        return remaining

    def collect_segments(self, max_payload, return_pending=False,
                         control_only=False):
        """
        Collect segments from channels for transmission.

        Implements the packing rules from CHANNEL_MANAGER.md:
        1. Channel 0 priority (control data first)
        2. Primary channel fill (round-robin selection)
        3. Round-robin fill for remaining space

        Args:
            max_payload: Max total segment bytes to collect
            return_pending: If True, return (segments, pending_data)
            control_only: If True, only collect control channel segments
        Returns:
            list: List of Segment instances if return_pending is False
            tuple: (segments, pending_data) if return_pending is True
        """
        segments = []
        remaining = max_payload
        pending_data = False
        # Step 1: Channel 0 data first (priority)
        if self._control.send_event.is_set():
            pending_data = True
        remaining = self._take_segment(
            CHANNEL_CONTROL,
            self._control,
            remaining,
            segments,
        )

        # Step 2: Snapshot active data channels and filter to those with data
        active_channels, channel_snapshot, inactive_ids = (
            self._snapshot_active_channels()
        )

        if active_channels:
            pending_data = True

        if inactive_ids:
            with self._lock:
                for cid in inactive_ids:
                    if cid in self._active_channels:
                        del self._active_channels[cid]
                self._update_data_send_event_locked()

        if not control_only:
            # Step 3: Primary channel fill (round-robin selection)
            if active_channels and remaining > SEGMENT_HEADER_SIZE:
                primary_id = active_channels[0]
                remaining = self._drain_channels(
                    [primary_id],
                    channel_snapshot,
                    remaining,
                    segments,
                )

                # Advance round-robin pointer (move primary to tail)
                with self._lock:
                    if primary_id in self._active_channels:
                        self._active_channels.pop(primary_id, None)
                        self._active_channels[primary_id] = None

                # Step 4: Fill remaining space from other channels (round-robin)
                if remaining > SEGMENT_HEADER_SIZE:
                    remaining = self._drain_channels(
                        active_channels[1:],
                        channel_snapshot,
                        remaining,
                        segments,
                    )

        if segments:
            payload_bytes = 0
            for seg in segments:
                payload_bytes += len(seg.data)
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.pack',
                    'Packed segments',
                    lambda: self._log_ctx(
                        None,
                        seg_count=len(segments),
                        payload_bytes=payload_bytes,
                        max_payload=max_payload,
                    ),
            )

        self._record_drain_stats(segments)
        if return_pending:
            return (segments, pending_data)
        return segments

    def _record_drain_stats(self, segments):
        """Record per-channel drain stats for debugging stalls."""
        if not self._config.stats_enabled:
            return
        if not logger.isEnabledFor(logging.DEBUG):
            return
        if not segments:
            return
        now = time_provider.now()
        with self._stats_lock:
            for seg in segments:
                cid = seg.channel
                self._stats_bytes_sent[cid] = (
                    self._stats_bytes_sent.get(cid, 0) + len(seg.data)
                )
            if now - self._stats_last_log < self._stats_interval:
                return
            if self._stats_bytes_sent:
                stats = {}
                total = 0
                for cid, count in self._stats_bytes_sent.items():
                    stats[str(cid)] = count
                    total += count
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.drain',
                        'Channel drain stats',
                        lambda: self._log_ctx(
                            None,
                            interval=self._stats_interval,
                            bytes_total=total,
                            bytes_by_channel=stats,
                        ),
                )
            self._stats_bytes_sent = {}
            self._stats_last_log = now

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
        now = None
        if self._id_reuse_cooldown > 0:
            now = time_provider.now()

        while True:
            if channel_id not in self._channels:
                if (self._id_reuse_cooldown <= 0 or
                        not self._is_in_reuse_cooldown(channel_id, now)):
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

    def _owns_channel_id(self, channel_id):
        if channel_id == CHANNEL_CONTROL:
            return False
        if self._is_alice:
            return is_alice_channel(channel_id)
        return is_bob_channel(channel_id)

    def _is_in_reuse_cooldown(self, channel_id, now):
        if not self._id_reuse_until:
            return False
        reuse_until = self._id_reuse_until.get(channel_id)
        if reuse_until is None:
            return False
        if now is None or now >= reuse_until:
            self._id_reuse_until.pop(channel_id, None)
            return False
        return True

    def _handle_open(self, msg):
        """Handle OPEN request from peer."""
        channel_id = msg.get('ch')

        if channel_id is None:
            return

        if (not isinstance(channel_id, integer_types) or
                channel_id < 1 or channel_id > 255):
            if logger.isEnabledFor(logging.WARNING):
                log_event(
                    logger,
                    logging.WARNING,
                    'channel.invalid_open',
                    'Invalid channel id in open request',
                    lambda: self._log_ctx(channel_id),
                )
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

        handler = self._channel_request_handler
        if handler is not None:
            try:
                accepted = bool(handler(channel_id))
            except Exception as exc:
                self._control.send_message(
                    ch_open_fail(channel_id, 'handler_error')
                )
                if logger.isEnabledFor(logging.WARNING):
                    log_event(
                        logger,
                        logging.WARNING,
                        'channel.open_reject',
                        'Channel open rejected by handler error',
                        lambda: self._log_ctx(
                            channel_id,
                            reason='handler_error',
                            error=repr(exc),
                        ),
                )
                return
            if not accepted:
                self._control.send_message(
                    ch_open_fail(channel_id, 'rejected')
                )
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.open_reject',
                        'Channel open rejected by handler',
                        lambda: self._log_ctx(channel_id, reason='rejected'),
                    )
                return

        with self._lock:
            if channel_id in self._channels:
                return
            channel = self._new_channel(channel_id, STATE_OPEN)
            self._register_channel_locked(channel)

        self._control.send_message(ch_open_ok(channel_id))
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger,
                logging.DEBUG,
                'channel.open_in',
                'Channel open received',
                lambda: self._log_ctx(channel_id),
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
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.open_ok',
                    'Channel open ok',
                    lambda: self._log_ctx(channel_id),
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
                self._unregister_channel_locked(channel_id)
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.open_fail',
                        'Channel open failed',
                        lambda: self._log_ctx(channel_id, reason=reason),
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
            self._unregister_channel_locked(channel_id)

        # Send CLOSE_OK (outside lock to avoid blocking)
        self._control.send_message(ch_close_ok(channel_id))
        if logger.isEnabledFor(logging.DEBUG):
            log_event(
                logger,
                logging.DEBUG,
                'channel.close_in',
                'Channel close received',
                lambda: self._log_ctx(channel_id),
            )

    def _handle_close_err(self, msg):
        """Handle CLOSE_ERR request from peer."""
        channel_id = msg.get('ch')
        if channel_id is None:
            return

        code = msg.get('code', 'remote_error')
        reason = msg.get('reason', 'Channel closed with error')

        with self._lock:
            channel = self._channels.get(channel_id)
        if channel is None:
            return
        channel._set_state(
            STATE_CLOSED,
            error=reason,
            error_code=code,
            drop_buffers=True,
        )
        with self._lock:
            self._unregister_channel_locked(channel_id)

        # Send CLOSE_OK (outside lock to avoid blocking)
        self._control.send_message(ch_close_ok(channel_id))
        if logger.isEnabledFor(logging.INFO):
            log_event(
                logger,
                logging.INFO,
                'channel.close_err_in',
                'Channel close error received',
                lambda: self._log_ctx(channel_id, code=code, reason=reason),
            )

    def _handle_half_close(self, msg):
        """Handle HALF_CLOSE request from peer."""
        channel_id = msg.get('ch')
        if channel_id is None:
            return

        with self._lock:
            channel = self._channels.get(channel_id)
        if channel is None:
            return
        if channel._set_recv_closed():
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.half_close_in',
                    'Channel half-close received',
                    lambda: self._log_ctx(channel_id),
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
                self._unregister_channel_locked(channel_id)
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.close_ok',
                        'Channel close ok',
                        lambda: self._log_ctx(channel_id),
                    )
