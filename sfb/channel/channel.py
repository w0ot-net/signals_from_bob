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

from ..compat import PY2, require_bytes_like, text_type, to_bytes, to_native_str
from ..logging_util import get_logger, log_event

logger = get_logger(__name__)

try:
    _buffer_type = buffer
except NameError:
    _buffer_type = None


def _is_buffer_view(value):
    if isinstance(value, memoryview):
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
            base = base.tobytes() if hasattr(base, 'tobytes') else base.tostring()
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
        'id', 'state', '_send_buf', '_recv_buf', '_lock',
        '_recv_event', '_send_space_event', '_closed_event', '_open_event',
        '_error', '_error_code',
        '_max_send_buf', '_max_recv_buf', '_send_buf_size', '_recv_buf_size',
        '_write_backoff_initial', '_write_backoff_max', '_close_callback',
        '_send_state_callback', '_send_state_seq', '_close_pending',
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
        self._lock = threading.Lock()
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
        self._send_state_callback = None
        self._send_state_seq = 0
        self._close_pending = False
        self._send_space_event.set()

    @property
    def is_open(self):
        """True if channel is open for data."""
        return self.state == STATE_OPEN

    @property
    def is_closed(self):
        """True if channel is fully closed."""
        return self.state == STATE_CLOSED

    @property
    def send_buf_size(self):
        """Current bytes queued for sending."""
        with self._lock:
            return self._send_buf_size

    @property
    def recv_buf_size(self):
        """Current bytes available for reading."""
        with self._lock:
            return self._recv_buf_size

    @property
    def error(self):
        """Error message if channel failed to open or closed with error."""
        return self._error

    @property
    def error_code(self):
        """Error code if channel closed with error."""
        return self._error_code

    def write(self, data):
        """
        Queue data for sending over the tunnel.

        Args:
            data: bytes to send

        Returns:
            int: number of bytes queued (may be less than len(data) if buffer partially full)

        Raises:
            ChannelError: if channel is not open or buffer completely full
        """
        data = _coerce_bytes_like(data)
        if not data:
            return 0

        notify = None
        max_send_buf = self._max_send_buf
        with self._lock:
            if self.state != STATE_OPEN:
                raise ChannelError('not_open', 'Channel not open')

            current_size = self._send_buf_size
            if max_send_buf is not None and current_size >= max_send_buf:
                self._send_space_event.clear()
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
        import time
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        while True:
            with self._lock:
                if self.state != STATE_OPEN:
                    raise ChannelError('not_open', 'Channel not open')
                if self._max_send_buf is None or self._send_buf_size < self._max_send_buf:
                    return True
            if deadline is not None:
                remaining = deadline - time.time()
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
        import time
        data = _coerce_bytes_like(data)
        if not data:
            return 0

        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        offset = 0
        total_len = len(data)
        backoff = self._write_backoff_initial
        max_backoff = self._write_backoff_max
        logged_wait = False

        while offset < total_len:
            # Check deadline
            if deadline is not None and time.time() >= deadline:
                raise ChannelError('timeout', 'Write timeout')

            # Check channel state
            if self.state != STATE_OPEN:
                raise ChannelError('not_open', 'Channel not open')

            view = _slice_view(data, offset, total_len - offset)
            try:
                sent = self.write(view)
            except ChannelError as e:
                if e.code != 'buffer_full':
                    raise
                if not logged_wait:
                    with self._lock:
                        current_size = self._send_buf_size
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
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise ChannelError('timeout', 'Write timeout')
                    wait_timeout = min(wait_timeout, remaining)
                if not self.wait_send_space(timeout=wait_timeout):
                    raise ChannelError('timeout', 'Write timeout')
                backoff = min(backoff * 1.5, max_backoff)
                continue

            if sent > 0:
                offset += sent
                backoff = self._write_backoff_initial
            else:
                wait_timeout = backoff
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise ChannelError('timeout', 'Write timeout')
                    wait_timeout = min(wait_timeout, remaining)
                if not self.wait_send_space(timeout=wait_timeout):
                    raise ChannelError('timeout', 'Write timeout')
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
            Empty bytes if channel closed cleanly.
            None on timeout.

        Raises:
            ChannelError: if channel closed with error
        """
        deadline = None
        if timeout is not None:
            import time
            deadline = time.time() + timeout

        while True:
            with self._lock:
                # Check for data
                if self._recv_buf:
                    return self._consume_recv(size)

                # Check for close/error
                if self.state == STATE_CLOSED:
                    if self._error:
                        code = self._error_code or 'closed'
                        raise ChannelError(code, self._error)
                    return b''

            # Wait for data or close
            if deadline is not None:
                import time
                remaining = deadline - time.time()
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

        import time
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        chunks = []
        remaining = size
        while remaining > 0:
            if deadline is None:
                chunk = self.read(remaining)
            else:
                remaining_time = deadline - time.time()
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

    def _consume_recv(self, size):
        """Consume up to size bytes from recv buffer. Must hold lock."""
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
        with self._lock:
            if self.state in (STATE_CLOSED, STATE_CLOSING):
                return
            if self.state in (STATE_OPEN, STATE_OPENING):
                self.state = STATE_CLOSING
                self._send_space_event.set()
                if self._send_buf_size == 0:
                    self._close_pending = False
                    callback = self._close_callback
                else:
                    self._close_pending = True
            else:
                self.state = STATE_CLOSED
                self._close_pending = False
                self._closed_event.set()
                self._open_event.set()
                self._recv_event.set()
                self._send_space_event.set()

        # Notify manager outside lock
        if callback:
            callback(self.id, None, None, False)

    def abort(self, code='aborted', message='Channel aborted'):
        """
        Abort channel immediately.

        Drops queued data, closes locally, and notifies peer with close_err.
        """
        callback = None
        with self._lock:
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
        return self.state == STATE_OPEN

    # --- Methods called by muxer ---

    def _set_state(self, state, error=None, error_code=None, drop_buffers=False):
        """Set channel state (called by muxer)."""
        notify = None
        with self._lock:
            self.state = state
            if error is not None:
                self._error = error
                self._error_code = error_code
            if drop_buffers:
                if self._send_buf_size:
                    self._send_buf.clear()
                    self._send_buf_size = 0
                    self._send_state_seq += 1
                    notify = (False, self._send_state_seq)
                self._send_space_event.set()
                if self._recv_buf_size:
                    self._recv_buf.clear()
                    self._recv_buf_size = 0
            if state == STATE_CLOSED:
                self._close_pending = False
                self._closed_event.set()
                self._open_event.set()  # Also signal open waiters (failed)
                self._recv_event.set()
                self._send_space_event.set()
            elif state == STATE_OPEN:
                self._open_event.set()  # Signal open waiters (success)
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
        with self._lock:
            if self.state not in (STATE_OPEN, STATE_CLOSING):
                return  # Discard data for non-open channels
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
        max_send_buf = self._max_send_buf
        with self._lock:
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
        if notify_close:
            callback = self._close_callback
            if callback is not None:
                callback(self.id, None, None, False)
        return result_data

    def _has_send_data(self):
        """Check if channel has data to send."""
        with self._lock:
            return bool(self._send_buf)

    def _get_send_state(self):
        """Return (has_data, seq) for send buffer state."""
        with self._lock:
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
