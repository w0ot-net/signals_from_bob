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

from ..compat import require_bytes, to_native_str
from ..logging_util import log_event

logger = logging.getLogger(__name__)


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
        '_recv_event', '_closed_event', '_open_event', '_error', '_max_send_buf',
        '_send_buf_size', '_recv_buf_size',
        '_write_backoff_initial', '_write_backoff_max', '_close_callback',
    )

    def __init__(self, channel_id, max_send_buf=65536,
                 write_backoff_initial=0.01, write_backoff_max=1.0):
        """
        Create a channel.

        Args:
            channel_id: Channel ID (0=control, odd=Alice, even=Bob)
            max_send_buf: Max bytes to buffer for sending
            write_backoff_initial: Initial backoff delay for write_wait
            write_backoff_max: Maximum backoff delay for write_wait
        """
        self.id = channel_id
        self.state = STATE_INIT
        self._send_buf = collections.deque()
        self._recv_buf = collections.deque()
        self._lock = threading.Lock()
        self._recv_event = threading.Event()
        self._closed_event = threading.Event()
        self._open_event = threading.Event()
        self._error = None
        self._max_send_buf = max_send_buf
        self._send_buf_size = 0
        self._recv_buf_size = 0
        self._write_backoff_initial = write_backoff_initial
        self._write_backoff_max = write_backoff_max
        self._close_callback = None

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
        data = require_bytes(data)
        if not data:
            return 0

        with self._lock:
            if self.state != STATE_OPEN:
                raise ChannelError('not_open', 'Channel not open')

            current_size = self._send_buf_size
            if current_size >= self._max_send_buf:
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.send_buf_full',
                    'Send buffer full',
                    {'ch': self.id, 'size': current_size, 'max': self._max_send_buf},
                )
                raise ChannelError('buffer_full', 'Send buffer full')

            # Limit to available space
            available = self._max_send_buf - current_size
            to_queue = data[:available]
            self._send_buf.append(bytes(to_queue))
            self._send_buf_size += len(to_queue)
            if len(to_queue) < len(data):
                log_event(
                    logger,
                    logging.DEBUG,
                    'channel.send_buf_high',
                    'Send buffer near capacity',
                    {
                        'ch': self.id,
                        'queued': len(to_queue),
                        'attempted': len(data),
                        'size': self._send_buf_size,
                        'max': self._max_send_buf,
                    },
                )
            return len(to_queue)

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
        data = require_bytes(data)
        if not data:
            return 0

        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        offset = 0
        backoff = self._write_backoff_initial
        max_backoff = self._write_backoff_max

        while offset < len(data):
            # Check deadline
            if deadline is not None and time.time() >= deadline:
                raise ChannelError('timeout', 'Write timeout')

            # Check channel state
            if self.state != STATE_OPEN:
                raise ChannelError('not_open', 'Channel not open')

            try:
                sent = self.write(data[offset:])
                if sent > 0:
                    offset += sent
                    backoff = self._write_backoff_initial
                else:
                    # No space, wait
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, max_backoff)
            except ChannelError as e:
                if e.code == 'buffer_full':
                    # Wait for buffer to drain
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, max_backoff)
                else:
                    raise

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
                        raise ChannelError('closed', self._error)
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
        data = require_bytes(data)
        if not data:
            return 0
        self.write_wait(data, timeout=timeout)
        return len(data)

    def _consume_recv(self, size):
        """Consume up to size bytes from recv buffer. Must hold lock."""
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

        Does not block. The muxer will send CLOSE and transition
        to CLOSED when CLOSE_OK is received.
        """
        callback = None
        with self._lock:
            if self.state in (STATE_CLOSED, STATE_CLOSING):
                return
            if self.state in (STATE_OPEN, STATE_OPENING):
                self.state = STATE_CLOSING
                callback = self._close_callback
            else:
                self.state = STATE_CLOSED
                self._closed_event.set()
                self._recv_event.set()

        # Notify manager outside lock
        if callback:
            callback(self.id)

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

    def _set_state(self, state, error=None):
        """Set channel state (called by muxer)."""
        with self._lock:
            self.state = state
            if error:
                self._error = error
            if state == STATE_CLOSED:
                self._closed_event.set()
                self._open_event.set()  # Also signal open waiters (failed)
                self._recv_event.set()
            elif state == STATE_OPEN:
                self._open_event.set()  # Signal open waiters (success)

    def _deliver(self, data):
        """Deliver received data to channel (called by muxer)."""
        data = require_bytes(data)
        if not data:
            return

        with self._lock:
            if self.state not in (STATE_OPEN, STATE_CLOSING):
                return  # Discard data for non-open channels
            self._recv_buf.append(bytes(data))
            self._recv_buf_size += len(data)

        self._recv_event.set()

    def _take_send_data(self, max_size):
        """
        Take data from send buffer for transmission.

        Args:
            max_size: max bytes to take

        Returns:
            bytes: data to send (may be empty)
        """
        with self._lock:
            if not self._send_buf:
                return b''

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

            return b''.join(result)

    def _has_send_data(self):
        """Check if channel has data to send."""
        with self._lock:
            return bool(self._send_buf)


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
