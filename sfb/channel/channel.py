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
import threading

from ..compat import require_bytes


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
    )

    def __init__(self, channel_id, max_send_buf=65536):
        """
        Create a channel.

        Args:
            channel_id: Channel ID (0=control, odd=Alice, even=Bob)
            max_send_buf: Max bytes to buffer for sending
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
            return sum(len(chunk) for chunk in self._send_buf)

    @property
    def recv_buf_size(self):
        """Current bytes available for reading."""
        with self._lock:
            return sum(len(chunk) for chunk in self._recv_buf)

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
            int: number of bytes queued

        Raises:
            ChannelError: if channel is not open or buffer full
        """
        data = require_bytes(data)
        if not data:
            return 0

        with self._lock:
            if self.state != STATE_OPEN:
                raise ChannelError('Channel not open')

            current_size = sum(len(chunk) for chunk in self._send_buf)
            if current_size >= self._max_send_buf:
                raise ChannelError('Send buffer full')

            # Limit to available space
            available = self._max_send_buf - current_size
            to_queue = data[:available]
            self._send_buf.append(bytes(to_queue))
            return len(to_queue)

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
                        raise ChannelError(self._error)
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

    def _consume_recv(self, size):
        """Consume up to size bytes from recv buffer. Must hold lock."""
        result = []
        remaining = size

        while remaining > 0 and self._recv_buf:
            chunk = self._recv_buf[0]
            if len(chunk) <= remaining:
                result.append(self._recv_buf.popleft())
                remaining -= len(chunk)
            else:
                result.append(chunk[:remaining])
                self._recv_buf[0] = chunk[remaining:]
                remaining = 0

        return b''.join(result)

    def close(self):
        """
        Initiate channel close.

        Does not block. The muxer will send CLOSE and transition
        to CLOSED when CLOSE_OK is received.
        """
        with self._lock:
            if self.state in (STATE_CLOSED, STATE_CLOSING):
                return
            if self.state == STATE_OPEN:
                self.state = STATE_CLOSING
            else:
                self.state = STATE_CLOSED
                self._closed_event.set()
                self._recv_event.set()

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
                    remaining -= len(chunk)
                else:
                    result.append(chunk[:remaining])
                    self._send_buf[0] = chunk[remaining:]
                    remaining = 0

            return b''.join(result)

    def _has_send_data(self):
        """Check if channel has data to send."""
        with self._lock:
            return bool(self._send_buf)


class ChannelError(Exception):
    """Channel operation error."""
    pass



def is_alice_channel(channel_id):
    """True if channel ID is allocated by Alice (odd, non-zero)."""
    return channel_id != 0 and (channel_id % 2) == 1


def is_bob_channel(channel_id):
    """True if channel ID is allocated by Bob (even, non-zero)."""
    return channel_id != 0 and (channel_id % 2) == 0
