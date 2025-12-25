# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from tunnel.channel import (
    Channel,
    ChannelError,
    ControlChannel,
    CHANNEL_CONTROL,
    STATE_OPEN,
    STATE_CLOSED,
)
from tunnel.compat import text_type


class ChannelTests(unittest.TestCase):
    def test_write_requires_open(self):
        ch = Channel(1)
        with self.assertRaises(ChannelError):
            ch.write(b'hi')

    def test_write_and_take_send_data(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        self.assertEqual(ch.write(b'abc'), 3)
        self.assertEqual(ch._take_send_data(2), b'ab')
        self.assertEqual(ch._take_send_data(2), b'c')
        self.assertEqual(ch._take_send_data(1), b'')

    def test_read_timeout_returns_none(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        self.assertIsNone(ch.read(1, timeout=0.01))

    def test_read_closed_returns_empty(self):
        ch = Channel(1)
        ch._set_state(STATE_CLOSED)
        self.assertEqual(ch.read(1, timeout=0.01), b'')

    def test_deliver_and_read(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch._deliver(b'hello')
        self.assertEqual(ch.read(5, timeout=0.01), b'hello')

    def test_deliver_requires_bytes(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        text = text_type('bad')
        with self.assertRaises(TypeError):
            ch._deliver(text)


class ControlChannelTests(unittest.TestCase):
    def test_control_channel_id(self):
        with self.assertRaises(ValueError):
            ControlChannel(1)
        ch = ControlChannel(CHANNEL_CONTROL)
        self.assertEqual(ch.id, CHANNEL_CONTROL)

    def test_send_message_encodes_json(self):
        ch = ControlChannel(CHANNEL_CONTROL)
        ch._set_state(STATE_OPEN)
        ch.send_message({'cmd': 'ping'})
        data = ch._take_send_data(1024)
        self.assertEqual(data, b'{"cmd":"ping"}\n')

    def test_recv_message_roundtrip(self):
        ch = ControlChannel(CHANNEL_CONTROL)
        ch._set_state(STATE_OPEN)
        ch._deliver(b'{"cmd":"ping"}\n')
        msg = ch.recv_message(timeout=0.01)
        self.assertEqual(msg, {'cmd': 'ping'})

    def test_recv_message_timeout(self):
        ch = ControlChannel(CHANNEL_CONTROL)
        ch._set_state(STATE_OPEN)
        self.assertIsNone(ch.recv_message(timeout=0.01))

    def test_recv_message_partial_close_raises(self):
        ch = ControlChannel(CHANNEL_CONTROL)
        ch._set_state(STATE_OPEN)
        ch._deliver(b'{"cmd":"ping"')
        ch._set_state(STATE_CLOSED)
        with self.assertRaises(ChannelError):
            ch.recv_message(timeout=0.01)


if __name__ == '__main__':
    unittest.main()
