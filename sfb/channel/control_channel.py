# -*- coding: ascii -*-
"""
Control channel helpers (channel 0).
"""

from __future__ import absolute_import

import json
import threading

from .channel import Channel, ChannelError, CHANNEL_CONTROL

CONTROL_MESSAGE_MAX_LENGTH = 0x1000


class ControlChannel(Channel):
    """
    Channel 0 control message helper.

    Control messages are JSON, one per line, ASCII encoded.
    """

    __slots__ = ('_line_buf', '_read_chunk_size', '_send_event')

    def __init__(self, channel_id=CHANNEL_CONTROL, max_send_buf=65536,
                 read_chunk_size=4096, write_backoff_initial=0.01,
                 write_backoff_max=1.0, send_event=None):
        if channel_id != CHANNEL_CONTROL:
            raise ValueError('ControlChannel must use channel 0')
        Channel.__init__(self, channel_id, max_send_buf=max_send_buf,
                         write_backoff_initial=write_backoff_initial,
                         write_backoff_max=write_backoff_max)
        self._line_buf = bytearray()
        self._read_chunk_size = read_chunk_size
        self._send_event = send_event or threading.Event()

    @property
    def send_event(self):
        return self._send_event

    def send_message(self, obj):
        from ..tunnel.tunnel_control_messages import encode as encode_message
        data = encode_message(obj)
        return self.write(data)

    def write(self, data):
        written = Channel.write(self, data)
        if written:
            self._send_event.set()
        return written

    def _take_send_data(self, max_size):
        data = Channel._take_send_data(self, max_size)
        if self.send_buf_size == 0:
            self._send_event.clear()
        return data

    def recv_message(self, timeout=None):
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

            chunk = self.read(self._read_chunk_size, timeout=timeout)
            if chunk is None:
                return None
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
