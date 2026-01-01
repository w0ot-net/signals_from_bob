# -*- coding: ascii -*-
"""
Control channel helpers (channel 0).
"""

from __future__ import absolute_import

import json
import logging
import threading

from .channel import Channel, ChannelError, CHANNEL_CONTROL
from ..logging_util import get_logger, log_event

logger = get_logger(__name__)

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
        """
        Take data without splitting control messages across packets.

        Control messages are newline-delimited JSON. To avoid truncating a JSON
        line when the transport payload cap is small, only return data through
        the last newline that fits within max_size. If there is no newline
        within the allowed window, send nothing; never emit partial control
        lines. If the first queued chunk is already larger than max_size and
        contains no newline (should not happen), raise a fatal ChannelError so
        the caller knows the control message cannot be transmitted safely.
        This preserves JSON integrity so downstream decoders never see
        truncated messages.
        """
        notify = None
        with self._lock:
            if not self._send_buf or max_size <= 0:
                return b''

            # Find the last newline within the allowed window.
            target_len = None
            offset = 0
            for chunk in self._send_buf:
                if offset >= max_size:
                    break
                take = min(len(chunk), max_size - offset)
                view = chunk[:take]
                nl_idx = view.rfind(b'\n')
                if nl_idx != -1:
                    target_len = offset + nl_idx + 1
                    break
                offset += take

            if target_len is None:
                msg_len = None
                scan_offset = 0
                for chunk in self._send_buf:
                    nl_pos = chunk.find(b'\n')
                    if nl_pos != -1:
                        msg_len = scan_offset + nl_pos + 1
                        break
                    scan_offset += len(chunk)
                if msg_len is not None:
                    if msg_len > max_size:
                        log_event(
                            logger,
                            logging.DEBUG,
                            'channel.control_send_cap',
                            'Control message exceeds payload cap',
                            lambda: {
                                'cap': max_size,
                                'needed': msg_len,
                                'send_buf_size': self._send_buf_size,
                            },
                        )
                    return b''
                first_chunk_len = len(self._send_buf[0])
                if first_chunk_len > max_size:
                    log_event(
                        logger,
                        logging.DEBUG,
                        'channel.control_send_partial',
                        'Control message missing newline',
                        lambda: {
                            'cap': max_size,
                            'first_chunk_len': first_chunk_len,
                            'send_buf_size': self._send_buf_size,
                        },
                    )
                return b''

            remaining = target_len
            parts = []
            while remaining > 0 and self._send_buf:
                chunk = self._send_buf[0]
                if len(chunk) <= remaining:
                    parts.append(self._send_buf.popleft())
                    self._send_buf_size -= len(chunk)
                    remaining -= len(chunk)
                else:
                    parts.append(chunk[:remaining])
                    self._send_buf[0] = chunk[remaining:]
                    self._send_buf_size -= remaining
                    remaining = 0

            if self._send_buf_size == 0:
                self._send_event.clear()
                self._send_state_seq += 1
                notify = (False, self._send_state_seq)
            data = b''.join(parts)

        if notify is not None:
            callback = self._send_state_callback
            if callback is not None:
                callback(self.id, notify[0], notify[1])
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
