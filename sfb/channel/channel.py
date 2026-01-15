# -*- coding: ascii -*-
"""
Channel - a logical TCP-like stream within the tunnel.

Each channel has:
- An ID (0=control, odd=Alice-opened, even=Bob-opened)
- State machine (INIT -> OPENING -> OPEN -> CLOSING -> CLOSED)
- Send/receive buffers
- Flow control integration with the muxer
"""

from __future__ import absolute_import

import collections
import logging
import threading

from ..compat import (
    PY2,
    bytes_from_view,
    require_bytes_like,
    text_type,
    to_bytes,
    to_native_str,
)
from ..logging_util import get_logger, log_event
from .. import time_provider

logger = get_logger(__name__)

try:
    _buffer_type = buffer
except NameError:
    _buffer_type = None


def _is_buffer_view(value):
    if isinstance(value, memoryview):
        if getattr(value, 'itemsize', None) != 1:
            return False
        return True
    if _buffer_type is not None and isinstance(value, _buffer_type):
        return True
    return False


def _coerce_bytes_like(value):
    if isinstance(value, text_type):
        raise TypeError('Expected bytes, got text')
    if _is_buffer_view(value):
        return value
    return require_bytes_like(value)


def _slice_view(value, offset, length):
    if length <= 0:
        return b''
    if PY2:
        base = value
        if isinstance(base, memoryview):
            base = bytes_from_view(base)
        elif isinstance(base, bytearray):
            base = bytes(base)
        if _buffer_type is not None and isinstance(base, _buffer_type):
            return buffer(base, offset, length)
        if not isinstance(base, bytes):
            base = require_bytes_like(base)
        if _buffer_type is not None:
            return buffer(base, offset, length)
        return base[offset:offset + length]
    view = value if isinstance(value, memoryview) else memoryview(value)
    return view[offset:offset + length]


# Channel states
STATE_INIT = 'init'          # Created but not yet opened
STATE_OPENING = 'opening'    # OPEN sent, waiting for OPEN_OK/OPEN_FAIL
STATE_OPEN = 'open'          # Ready for data
STATE_CLOSING = 'closing'    # CLOSE sent, waiting for CLOSE_OK
STATE_CLOSED = 'closed'      # Fully closed

# Channel ID conventions
CHANNEL_CONTROL = 0


class Channel(object):
    """
    A logical bidirectional stream within the tunnel.

    Provides a TCP-like interface for reading and writing data.
    The muxer handles actual transmission over the tunnel.
    """

    __slots__ = (
        'id', 'state', '_send_buf', '_recv_buf',
        '_state_lock', '_send_lock', '_recv_lock',
        '_recv_event', '_send_space_event', '_closed_event', '_open_event',
        '_error', '_error_code',
        '_max_send_buf', '_max_recv_buf', '_send_buf_size', '_recv_buf_size',
        '_write_backoff_initial', '_write_backoff_max', '_close_callback',
        '_send_state_callback', '_send_state_seq', '_close_pending',
        '_half_close_callback', '_half_close_pending',
        '_send_closed', '_recv_closed',
    )

    def __init__(self, channel_id, max_send_buf=1048576, max_recv_buf=1048576,
                 write_backoff_initial=0.01, write_backoff_max=1.0):
        """
        Create a channel.

        Args:
            channel_id: Channel ID (0=control, odd=Alice, even=Bob)
            max_send_buf: Max bytes to buffer for sending
            max_recv_buf: Max bytes to buffer for receiving
            write_backoff_initial: Initial backoff delay for write_wait
            write_backoff_max: Maximum backoff delay for write_wait
        """
        self.id = channel_id
        self.state = STATE_INIT
        self._send_buf = collections.deque()
        self._recv_buf = collections.deque()
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()
        # Lock ordering: state -> send -> recv. Never take state while holding send/recv.
        self._recv_event = threading.Event()
        self._send_space_event = threading.Event()
        self._closed_event = threading.Event()
        self._open_event = threading.Event()
        self._error = None
        self._error_code = None
        self._max_send_buf = max_send_buf
        self._max_recv_buf = max_recv_buf
        self._send_buf_size = 0
        self._recv_buf_size = 0
        self._write_backoff_initial = write_backoff_initial
        self._write_backoff_max = write_backoff_max
        self._close_callback = None
        self._half_close_callback = None
        self._send_state_callback = None
        self._send_state_seq = 0
        self._close_pending = False
        self._half_close_pending = False
        self._send_closed = False
        self._recv_closed = False
        self._send_space_event.set()

    @property
    def is_open(self):
        """True if channel is open for data."""
        with self._state_lock:
            return self.state == STATE_OPEN

    @property
    def is_closed(self):
        """True if channel is fully closed."""
        with self._state_lock:
            return self.state == STATE_CLOSED

    @property
    def send_buf_size(self):
        """Current bytes queued for sending."""
        with self._send_lock:
            return self._send_buf_size

    @property
    def recv_buf_size(self):
        """Current bytes available for reading."""
        with self._recv_lock:
            return self._recv_buf_size

    @property
    def send_closed(self):
        """True if the send side is closed."""
        with self._send_lock:
            return self._send_closed

    @property
    def recv_closed(self):
        """True if the receive side is closed."""
        with self._recv_lock:
            return self._recv_closed

    @property
    def error(self):
        """Error message if channel failed to open or closed with error."""
        with self._state_lock:
            return self._error

    @property
    def error_code(self):
        """Error code if channel closed with error."""
        with self._state_lock:
            return self._error_code

    def write(self, data):
        """
        Queue data for sending over the tunnel.

        Args:
            data: bytes to send

        Returns:
            int: number of bytes queued (may be less than len(data) if buffer partially full)

        Raises:
            ChannelError: if channel is not open, send side closed, or buffer full
        """
        data = _coerce_bytes_like(data)
        if not data:
            return 0

        notify = None
        max_send_buf = self._max_send_buf
        with self._state_lock:
            if self.state != STATE_OPEN:
                raise ChannelError('not_open', 'Channel not open')
            with self._send_lock:
                if self._send_closed:
                    raise ChannelError('send_closed', 'Send side closed')

                current_size = self._send_buf_size
                if max_send_buf is not None and current_size >= max_send_buf:
                    self._send_space_event.clear()
                    if logger.isEnabledFor(logging.DEBUG):
                        log_event(
                            logger,
                            logging.DEBUG,
                            'channel.send_buf_full',
                            'Send buffer full',
                            lambda: {'ch': self.id, 'size': current_size, 'max': max_send_buf},
                        )
                    raise ChannelError('buffer_full', 'Send buffer full')

                # Limit to available space
                was_empty = (current_size == 0)
                if max_send_buf is None:
                    available = len(data)
                else:
                    available = max_send_buf - current_size
                    if available <= 0:
                        self._send_space_event.clear()
                        raise ChannelError('buffer_full', 'Send buffer full')
                data_len = len(data)
                if available >= data_len:
                    to_queue = data
                    queued_len = data_len
                else:
                    to_queue = _slice_view(data, 0, available)
                    queued_len = available
                self._send_buf.append(to_bytes(to_queue))
                self._send_buf_size += queued_len
                if was_empty and self._send_buf_size > 0:
                    self._send_state_seq += 1
                    notify = (True, self._send_state_seq)
                if queued_len < data_len:
                    if logger.isEnabledFor(logging.DEBUG):
                        log_event(
                            logger,
                            logging.DEBUG,
                            'channel.send_buf_high',
                            'Send buffer near capacity',
                            lambda: {
                                'ch': self.id,
                                'queued': queued_len,
                                'attempted': data_len,
                                'size': self._send_buf_size,
                                'max': max_send_buf,
                            },
                        )
                if max_send_buf is None or self._send_buf_size < max_send_buf:
                    self._send_space_event.set()
                else:
                    self._send_space_event.clear()
                written = queued_len

        if notify is not None:
            callback = self._send_state_callback
            if callback is not None:
                callback(self.id, notify[0], notify[1])
        return written

    def wait_send_space(self, timeout=None):
        """
        Wait for send buffer space to become available.

        Args:
            timeout: max seconds to wait (None = wait forever)

        Returns:
            bool: True if space is available, False on timeout

        Raises:
            ChannelError: if channel is not open
        """
        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout

        while True:
            with self._state_lock:
                if self.state != STATE_OPEN:
                    raise ChannelError('not_open', 'Channel not open')
                with self._send_lock:
                    if self._send_closed:
                        raise ChannelError('send_closed', 'Send side closed')
                    if (self._max_send_buf is None or
                            self._send_buf_size < self._max_send_buf):
                        return True
            if deadline is not None:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return False
                self._send_space_event.wait(timeout=remaining)
            else:
                self._send_space_event.wait()

    def write_wait(self, data, timeout=None):
        """
        Write data, waiting for buffer space if needed.

        Uses exponential backoff while waiting for buffer to drain.
        More efficient than caller doing retries.

        Args:
            data: bytes to send
            timeout: max seconds to wait (None=wait forever)

        Returns:
            int: number of bytes written (always len(data) on success)

        Raises:
            ChannelError: if channel closes or timeout expires
        """
        data = _coerce_bytes_like(data)
        if not data:
            return 0

        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout

        offset = 0
        total_len = len(data)
        backoff = self._write_backoff_initial
        max_backoff = self._write_backoff_max
        logged_wait = False

        while offset < total_len:
            # Check deadline
            if deadline is not None and time_provider.now() >= deadline:
                raise ChannelError('timeout', 'Write timeout')

            view = _slice_view(data, offset, total_len - offset)
            try:
                sent = self.write(view)
            except ChannelError as e:
                if e.code != 'buffer_full':
                    raise
                if not logged_wait:
                    with self._send_lock:
                        current_size = self._send_buf_size
                    if logger.isEnabledFor(logging.DEBUG):
                        log_event(
                            logger,
                            logging.DEBUG,
                            'channel.write_wait',
                            'Waiting for send buffer space',
                            lambda: {
                                'ch': self.id,
                                'size': current_size,
                                'max': self._max_send_buf,
                                'backoff': backoff,
                            },
                        )
                    logged_wait = True
                wait_timeout = backoff
                if deadline is not None:
                    remaining = deadline - time_provider.now()
                    if remaining <= 0:
                        raise ChannelError('timeout', 'Write timeout')
                    wait_timeout = min(wait_timeout, remaining)
                self.wait_send_space(timeout=wait_timeout)
                backoff = min(backoff * 1.5, max_backoff)
                continue

            if sent > 0:
                offset += sent
                backoff = self._write_backoff_initial
            else:
                wait_timeout = backoff
                if deadline is not None:
                    remaining = deadline - time_provider.now()
                    if remaining <= 0:
                        raise ChannelError('timeout', 'Write timeout')
                    wait_timeout = min(wait_timeout, remaining)
                self.wait_send_space(timeout=wait_timeout)
                backoff = min(backoff * 1.5, max_backoff)

        return len(data)

    def read(self, size, timeout=None):
        """
        Read data received from the tunnel.

        Args:
            size: max bytes to read
            timeout: seconds to wait for data (None=block forever)

        Returns:
            bytes: data read (may be less than size)
            Empty bytes if receive side closed cleanly.
            None on timeout.

        Raises:
            ChannelError: if channel closed with error
        """
        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout

        while True:
            with self._recv_lock:
                # Check for data
                if self._recv_buf:
                    return self._consume_recv(size)
                recv_closed = self._recv_closed

            # Check for close/error
            with self._state_lock:
                state = self.state
                error = self._error
                error_code = self._error_code
            if state == STATE_CLOSED:
                if error:
                    code = error_code or 'closed'
                    raise ChannelError(code, error)
                return b''
            if recv_closed:
                return b''

            # Wait for data or close
            if deadline is not None:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return None
                got_event = self._recv_event.wait(timeout=remaining)
            else:
                self._recv_event.wait()

            self._recv_event.clear()

    def read_exact(self, size, timeout=None):
        """
        Read exactly size bytes, blocking until all data arrives.

        Args:
            size: number of bytes to read
            timeout: seconds to wait for all data (None=block forever)

        Returns:
            bytes: exactly size bytes

        Raises:
            ChannelError: on timeout or close before size is received
        """
        if size <= 0:
            return b''

        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout

        chunks = []
        remaining = size
        while remaining > 0:
            if deadline is None:
                chunk = self.read(remaining)
            else:
                remaining_time = deadline - time_provider.now()
                if remaining_time <= 0:
                    raise ChannelError('timeout', 'Read timeout')
                chunk = self.read(remaining, timeout=remaining_time)
            if chunk is None:
                raise ChannelError('timeout', 'Read timeout')
            if chunk == b'':
                raise ChannelError('closed', 'Channel closed')
            chunks.append(chunk)
            remaining -= len(chunk)

        return b''.join(chunks)

    def write_all(self, data, timeout=None):
        """
        Write all data, blocking until fully queued or timeout.

        Args:
            data: bytes to send
            timeout: max seconds to wait (None=wait forever)

        Returns:
            int: number of bytes written

        Raises:
            ChannelError: on timeout or close
        """
        data = require_bytes_like(data)
        if not data:
            return 0
        self.write_wait(data, timeout=timeout)
        return len(data)

    def close_write(self):
        """
        Half-close the send side of the channel.

        Marks the send side closed and emits a half_close control message
        after queued data drains.
        """
        callback = None
        pending_size = None
        state = None
        send_closed_before = None
        recv_closed = None
        immediate = False
        with self._state_lock:
            state = self.state
            with self._send_lock:
                if self._send_closed:
                    return
                if state != STATE_OPEN:
                    raise ChannelError('not_open', 'Channel not open')
                send_closed_before = self._send_closed
                self._send_closed = True
                self._send_space_event.set()
                pending_size = self._send_buf_size
                if pending_size == 0:
                    self._half_close_pending = False
                    callback = self._half_close_callback
                    immediate = True
                else:
                    self._half_close_pending = True
        with self._recv_lock:
            recv_closed = self._recv_closed

        if pending_size is not None:
            if logger.isEnabledFor(logging.DEBUG):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.half_close_request',
                    'Half-close requested',
                    lambda: {
                        'ch': self.id,
                        'pending_bytes': pending_size,
                        'state': state,
                        'send_closed_before': send_closed_before,
                        'recv_closed': recv_closed,
                        'immediate': immediate,
                    },
                )
            if not immediate and pending_size:
                if logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.half_close_pending_close',
                        'Half-close pending until send buffer drains',
                        lambda: {'ch': self.id, 'pending_bytes': pending_size},
                    )
        if callback is not None:
            callback(self.id)

    def _consume_recv(self, size):
        """Consume up to size bytes from recv buffer. Must hold recv lock."""
        if size <= 0 or not self._recv_buf:
            return b''
        chunk = self._recv_buf[0]
        chunk_len = len(chunk)
        if size <= chunk_len:
            if size == chunk_len:
                self._recv_buf.popleft()
                self._recv_buf_size -= chunk_len
                return chunk
            self._recv_buf[0] = chunk[size:]
            self._recv_buf_size -= size
            return chunk[:size]
        if len(self._recv_buf) == 1:
            self._recv_buf.popleft()
            self._recv_buf_size -= chunk_len
            return chunk

        result = []
        remaining = size

        while remaining > 0 and self._recv_buf:
            chunk = self._recv_buf[0]
            if len(chunk) <= remaining:
                result.append(self._recv_buf.popleft())
                self._recv_buf_size -= len(chunk)
                remaining -= len(chunk)
            else:
                result.append(chunk[:remaining])
                self._recv_buf[0] = chunk[remaining:]
                self._recv_buf_size -= remaining
                remaining = 0

        return b''.join(result)

    def close(self):
        """
        Initiate channel close.

        Does not block. The muxer will send CLOSE after queued
        send data drains and transition to CLOSED when CLOSE_OK
        is received.
        """
        callback = None
        with self._state_lock:
            if self.state in (STATE_CLOSED, STATE_CLOSING):
                return
            if self.state in (STATE_OPEN, STATE_OPENING):
                self.state = STATE_CLOSING
                with self._send_lock:
                    self._send_space_event.set()
                    if self._send_buf_size == 0:
                        self._close_pending = False
                        callback = self._close_callback
                    else:
                        self._close_pending = True
            else:
                self.state = STATE_CLOSED
                with self._send_lock:
                    self._close_pending = False
                    self._half_close_pending = False
                    self._send_closed = True
                    self._send_space_event.set()
                with self._recv_lock:
                    self._recv_closed = True
                    self._recv_event.set()
                self._closed_event.set()
                self._open_event.set()

        # Notify manager outside lock
        if callback:
            callback(self.id, None, None, False)

    def abort(self, code='aborted', message='Channel aborted'):
        """
        Abort channel immediately.

        Drops queued data, closes locally, and notifies peer with close_err.
        """
        callback = None
        with self._state_lock:
            if self.state == STATE_CLOSED:
                return
            send_abort = self.state in (STATE_OPEN, STATE_OPENING, STATE_CLOSING)
        self._set_state(
            STATE_CLOSED,
            error=message,
            error_code=code,
            drop_buffers=True,
        )
        if send_abort:
            callback = self._close_callback
        if callback:
            callback(self.id, code, message, True)

    def wait_closed(self, timeout=None):
        """
        Wait for channel to fully close.

        Args:
            timeout: max seconds to wait

        Returns:
            bool: True if closed, False if timeout
        """
        return self._closed_event.wait(timeout=timeout)

    def wait_open(self, timeout=None):
        """
        Wait for channel to open.

        Args:
            timeout: max seconds to wait (None = forever)

        Returns:
            bool: True if channel is now OPEN, False if CLOSED/failed/timeout
        """
        if not self._open_event.wait(timeout=timeout):
            return False  # Timeout
        with self._state_lock:
            return self.state == STATE_OPEN

    # --- Methods called by muxer ---

    def _set_recv_closed(self):
        """Mark the receive side closed (called by muxer)."""
        with self._state_lock:
            if self.state == STATE_CLOSED:
                return False
            with self._recv_lock:
                if self._recv_closed:
                    return False
                self._recv_closed = True
                self._recv_event.set()
        return True

    def _set_state(self, state, error=None, error_code=None, drop_buffers=False):
        """Set channel state (called by muxer)."""
        notify = None
        with self._state_lock:
            self.state = state
            if error is not None:
                self._error = error
                self._error_code = error_code
            if drop_buffers:
                with self._send_lock:
                    if self._send_buf_size:
                        self._send_buf.clear()
                        self._send_buf_size = 0
                        self._send_state_seq += 1
                        notify = (False, self._send_state_seq)
                    self._send_space_event.set()
                with self._recv_lock:
                    if self._recv_buf_size:
                        self._recv_buf.clear()
                        self._recv_buf_size = 0
            if state == STATE_CLOSED:
                with self._send_lock:
                    self._close_pending = False
                    self._half_close_pending = False
                    self._send_closed = True
                    self._send_space_event.set()
                with self._recv_lock:
                    self._recv_closed = True
                    self._recv_event.set()
                self._closed_event.set()
                self._open_event.set()  # Also signal open waiters (failed)
            elif state == STATE_OPEN:
                self._open_event.set()  # Signal open waiters (success)
                with self._send_lock:
                    self._send_space_event.set()
        if notify is not None:
            callback = self._send_state_callback
            if callback is not None:
                callback(self.id, notify[0], notify[1])

    def _deliver(self, data):
        """Deliver received data to channel (called by muxer)."""
        data = require_bytes_like(data)
        if not data:
            return

        overflow = False
        with self._state_lock:
            state = self.state
        if state not in (STATE_OPEN, STATE_CLOSING):
            return  # Discard data for non-open channels
        with self._recv_lock:
            if self._recv_closed:
                return
            if (self._max_recv_buf is not None and
                    self._recv_buf_size + len(data) > self._max_recv_buf):
                overflow = True
            else:
                if isinstance(data, bytes):
                    chunk = data
                else:
                    chunk = bytes(data)
                self._recv_buf.append(chunk)
                self._recv_buf_size += len(data)

        if overflow:
            raise ChannelError('recv_overflow', 'Receive buffer overflow')
        self._recv_event.set()

    def _take_send_data(self, max_size):
        """
        Take data from send buffer for transmission.

        Args:
            max_size: max bytes to take

        Returns:
            bytes: data to send (may be empty)
        """
        notify_send = None
        notify_close = False
        notify_half_close = False
        max_send_buf = self._max_send_buf
        with self._send_lock:
            if not self._send_buf:
                return b''

            if max_size <= 0:
                return b''
            chunk = self._send_buf[0]
            chunk_len = len(chunk)
            if max_size <= chunk_len:
                if max_size == chunk_len:
                    data = self._send_buf.popleft()
                    self._send_buf_size -= chunk_len
                else:
                    data = chunk[:max_size]
                    self._send_buf[0] = chunk[max_size:]
                    self._send_buf_size -= max_size
                if self._send_buf_size == 0:
                    self._send_state_seq += 1
                    notify_send = (False, self._send_state_seq)
                    if self._half_close_pending:
                        self._half_close_pending = False
                        notify_half_close = True
                    if self._close_pending:
                        self._close_pending = False
                        notify_close = True
                result_data = data
            elif len(self._send_buf) == 1:
                data = self._send_buf.popleft()
                self._send_buf_size -= chunk_len
                if self._send_buf_size == 0:
                    self._send_state_seq += 1
                    notify_send = (False, self._send_state_seq)
                    if self._half_close_pending:
                        self._half_close_pending = False
                        notify_half_close = True
                    if self._close_pending:
                        self._close_pending = False
                        notify_close = True
                result_data = data
            else:
                result = []
                remaining = max_size

                while remaining > 0 and self._send_buf:
                    chunk = self._send_buf[0]
                    if len(chunk) <= remaining:
                        result.append(self._send_buf.popleft())
                        self._send_buf_size -= len(chunk)
                        remaining -= len(chunk)
                    else:
                        result.append(chunk[:remaining])
                        self._send_buf[0] = chunk[remaining:]
                        self._send_buf_size -= remaining
                        remaining = 0

                if self._send_buf_size == 0:
                    self._send_state_seq += 1
                    notify_send = (False, self._send_state_seq)
                    if self._half_close_pending:
                        self._half_close_pending = False
                        notify_half_close = True
                    if self._close_pending:
                        self._close_pending = False
                        notify_close = True
                result_data = b''.join(result)
            if max_send_buf is None or self._send_buf_size < max_send_buf:
                self._send_space_event.set()
            else:
                self._send_space_event.clear()

        if notify_send is not None:
            callback = self._send_state_callback
            if callback is not None:
                callback(self.id, notify_send[0], notify_send[1])
        if notify_half_close:
            callback = self._half_close_callback
            if callback is not None:
                callback(self.id)
        if notify_close:
            callback = self._close_callback
            if callback is not None:
                callback(self.id, None, None, False)
        return result_data

    def _has_send_data(self):
        """Check if channel has data to send."""
        with self._send_lock:
            return bool(self._send_buf)

    def _get_send_state(self):
        """Return (has_data, seq) for send buffer state."""
        with self._send_lock:
            return bool(self._send_buf), self._send_state_seq

    def _set_send_state_callback(self, callback):
        """Set send buffer state callback (called on empty/non-empty transitions)."""
        self._send_state_callback = callback


class ChannelError(Exception):
    """Channel operation error."""
    def __init__(self, code, message=None):
        if message is None:
            message = code
            code = 'io'
        Exception.__init__(self, message)
        self.code = code
        self.message = message

    def __str__(self):
        return to_native_str(self.message)



def is_alice_channel(channel_id):
    """True if channel ID is allocated by Alice (odd, non-zero)."""
    return channel_id != 0 and (channel_id % 2) == 1


def is_bob_channel(channel_id):
    """True if channel ID is allocated by Bob (even, non-zero)."""
    return channel_id != 0 and (channel_id % 2) == 0
