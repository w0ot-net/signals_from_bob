# -*- coding: ascii -*-
"""
Control channel helpers (channel 0).
"""

from __future__ import absolute_import

import json
import threading

from .channel import Channel, CHANNEL_CONTROL, ChannelError
from .. import time_provider

CONTROL_MESSAGE_MAX_LENGTH = 0x1000
CONTROL_READ_CHUNK_SIZE = 4096


class ControlChannel(Channel):
    """
    Channel 0 control message helper.

    Control messages are JSON, one per line, ASCII encoded.
    """

    __slots__ = (
        '_line_buf',
        '_read_chunk_size',
        '_send_event',
        '_send_event_callback',
    )

    def __init__(self, channel_id=CHANNEL_CONTROL, max_send_buf=1048576,
                 max_recv_buf=1048576, write_backoff_initial=0.01,
                 write_backoff_max=1.0, send_event=None,
                 send_event_callback=None):
        if channel_id != CHANNEL_CONTROL:
            raise ValueError('ControlChannel must use channel 0')
        Channel.__init__(self, channel_id, max_send_buf=max_send_buf,
                         max_recv_buf=max_recv_buf,
                         write_backoff_initial=write_backoff_initial,
                         write_backoff_max=write_backoff_max)
        self._line_buf = bytearray()
        self._read_chunk_size = CONTROL_READ_CHUNK_SIZE
        self._send_event = send_event or threading.Event()
        self._send_event_callback = send_event_callback

    @property
    def send_event(self):
        return self._send_event

    def _set_send_event(self, is_set):
        if is_set:
            self._send_event.set()
        else:
            self._send_event.clear()
        if self._send_event_callback is not None:
            self._send_event_callback(is_set)

    def send_message(self, obj):
        from ..tunnel.tunnel_control_messages import encode as encode_message
        data = encode_message(obj)
        return self.write(data)

    def close(self):
        """Control channel lifetime is tied to the tunnel."""
        return

    def abort(self, code='aborted', message='Channel aborted'):
        """Ignore abort requests for control channel."""
        return

    def close_write(self):
        """Ignore half-close requests for control channel."""
        return

    def write(self, data):
        written = Channel.write(self, data)
        if written:
            self._set_send_event(True)
        return written

    def _take_send_data(self, max_size):
        """
        Take data for transmission.

        Control messages are newline-delimited JSON and may be split across
        multiple segments when the payload cap is small. The receiver buffers
        partial data until a newline terminator arrives.
        """
        data = Channel._take_send_data(self, max_size)
        with self._lock:
            if self._send_buf_size == 0:
                self._set_send_event(False)
        return data

    def recv_message(self, timeout=None):
        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout
        recv_seq = self._get_recv_seq()

        while True:
            line = self._pop_line()
            if line is not None:
                if len(line) > CONTROL_MESSAGE_MAX_LENGTH:
                    raise ChannelError('invalid', 'Control message too long')
                if not line:
                    continue
                try:
                    return json.loads(line.decode('ascii'))
                except ValueError as e:
                    raise ChannelError('invalid', 'Invalid control message: %s' % e)

            if len(self._line_buf) > CONTROL_MESSAGE_MAX_LENGTH:
                raise ChannelError('invalid', 'Control message too long')

            if deadline is not None:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return None
            else:
                remaining = None

            next_seq = self.wait_recv_seq(recv_seq, timeout=remaining)
            if next_seq is None:
                return None
            recv_seq = next_seq

            chunk = self.read(self._read_chunk_size, timeout=0)
            if chunk is None:
                continue
            if chunk == b'':
                if self._line_buf:
                    raise ChannelError('closed', 'Control channel closed with partial message')
                return None
            self._line_buf.extend(chunk)
            newline_idx = self._line_buf.find(b'\n')
            if newline_idx == -1:
                if len(self._line_buf) > CONTROL_MESSAGE_MAX_LENGTH:
                    raise ChannelError('invalid', 'Control message too long')
            elif newline_idx > CONTROL_MESSAGE_MAX_LENGTH:
                raise ChannelError('invalid', 'Control message too long')

    def _pop_line(self):
        idx = self._line_buf.find(b'\n')
        if idx == -1:
            return None
        line = bytes(self._line_buf[:idx])
        del self._line_buf[:idx + 1]
        return line
