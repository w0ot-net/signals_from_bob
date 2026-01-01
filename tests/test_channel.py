# -*- coding: ascii -*-
from __future__ import absolute_import

import json
import unittest

import sfb.channel.channel_manager as channel_manager_module
from sfb.channel.channel import (
    Channel,
    ChannelError,
    STATE_OPEN,
    STATE_OPENING,
    STATE_CLOSING,
    STATE_CLOSED,
    is_alice_channel,
    is_bob_channel,
)
from sfb.channel.control_channel import ControlChannel, CONTROL_MESSAGE_MAX_LENGTH
from sfb.channel.channel_manager import ChannelManager
from sfb.config import Config
from sfb.protocol import Segment, SEGMENT_HEADER_SIZE, CHANNEL_CONTROL


def make_test_config(**overrides):
    """Create a Config for testing with sensible defaults."""
    defaults = {
        'dns_base_domain': 'test.local',
    }
    defaults.update(overrides)
    return Config(**defaults)


class ChannelTests(unittest.TestCase):
    def test_write_requires_open(self):
        ch = Channel(1)
        with self.assertRaises(ChannelError) as ctx:
            ch.write(b'abc')
        self.assertEqual(ctx.exception.code, 'not_open')

    def test_write_respects_max_send_buffer(self):
        ch = Channel(1, max_send_buf=4)
        ch._set_state(STATE_OPEN)
        self.assertEqual(ch.write(b'abcd'), 4)
        with self.assertRaises(ChannelError) as ctx:
            ch.write(b'e')
        self.assertEqual(ctx.exception.code, 'buffer_full')

    def test_read_timeout(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        self.assertIsNone(ch.read(1, timeout=0.01))

    def test_deliver_and_read(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch._deliver(b'hello')
        data = ch.read(10, timeout=0.1)
        self.assertEqual(data, b'hello')

    def test_close_state_transitions(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch.close()
        self.assertEqual(ch.state, STATE_CLOSING)
        ch._set_state(STATE_CLOSED)
        self.assertTrue(ch.wait_closed(timeout=0.1))
        self.assertTrue(ch.is_closed)

    def test_close_opening_triggers_callback(self):
        ch = Channel(1)
        called = []
        ch._close_callback = lambda cid: called.append(cid)
        ch._set_state(STATE_OPENING)
        ch.close()
        self.assertEqual(ch.state, STATE_CLOSING)
        self.assertEqual(called, [1])

    def test_read_closed_with_error(self):
        ch = Channel(1)
        ch._set_state(STATE_CLOSED, error='boom')
        with self.assertRaises(ChannelError) as ctx:
            ch.read(1, timeout=0.1)
        self.assertEqual(ctx.exception.code, 'closed')
        self.assertEqual(ctx.exception.message, 'boom')

    def test_take_send_data_slices(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch.write(b'abcdef')
        part = ch._take_send_data(3)
        self.assertEqual(part, b'abc')
        rest = ch._take_send_data(10)
        self.assertEqual(rest, b'def')

    def test_channel_id_helpers(self):
        self.assertTrue(is_alice_channel(1))
        self.assertFalse(is_alice_channel(2))
        self.assertTrue(is_bob_channel(2))
        self.assertFalse(is_bob_channel(1))

    def test_wait_open_success(self):
        """wait_open returns True when channel opens."""
        ch = Channel(1)
        ch._set_state(STATE_OPENING)
        # Simulate peer accepting
        ch._set_state(STATE_OPEN)
        self.assertTrue(ch.wait_open(timeout=0.1))
        self.assertTrue(ch.is_open)

    def test_wait_open_failure(self):
        """wait_open returns False when channel fails to open."""
        ch = Channel(1)
        ch._set_state(STATE_OPENING)
        # Simulate peer rejecting
        ch._set_state(STATE_CLOSED, error='rejected')
        self.assertFalse(ch.wait_open(timeout=0.1))
        self.assertTrue(ch.is_closed)
        self.assertEqual(ch.error, 'rejected')

    def test_wait_open_timeout(self):
        """wait_open returns False on timeout."""
        ch = Channel(1)
        ch._set_state(STATE_OPENING)
        # Don't transition state - should timeout
        self.assertFalse(ch.wait_open(timeout=0.01))
        self.assertEqual(ch.state, STATE_OPENING)

    def test_error_property(self):
        """error property exposes failure reason."""
        ch = Channel(1)
        self.assertIsNone(ch.error)
        ch._set_state(STATE_CLOSED, error='connection refused')
        self.assertEqual(ch.error, 'connection refused')

    def test_read_exact_success(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch._deliver(b'hello')
        data = ch.read_exact(5, timeout=0.1)
        self.assertEqual(data, b'hello')

    def test_read_exact_timeout(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        with self.assertRaises(ChannelError) as ctx:
            ch.read_exact(1, timeout=0.01)
        self.assertEqual(ctx.exception.code, 'timeout')

    def test_read_exact_closed(self):
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch._set_state(STATE_CLOSED)
        with self.assertRaises(ChannelError) as ctx:
            ch.read_exact(1, timeout=0.1)
        self.assertEqual(ctx.exception.code, 'closed')

    def test_write_all_timeout(self):
        ch = Channel(1, max_send_buf=1)
        ch._set_state(STATE_OPEN)
        ch.write(b'a')
        with self.assertRaises(ChannelError) as ctx:
            ch.write_all(b'b', timeout=0.01)
        self.assertEqual(ctx.exception.code, 'timeout')


class ControlChannelTests(unittest.TestCase):
    def test_send_recv_message_roundtrip(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl.send_message({'t': 'tun', 'c': 'noop'})
        data = ctrl._take_send_data(1024)
        ctrl._deliver(data)
        msg = ctrl.recv_message(timeout=0.1)
        self.assertEqual(msg, {'t': 'tun', 'c': 'noop'})

    def test_recv_message_invalid_json(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl._deliver(b'{bad}\n')
        with self.assertRaises(ChannelError) as ctx:
            ctrl.recv_message(timeout=0.1)
        self.assertEqual(ctx.exception.code, 'invalid')

    def test_recv_message_partial_on_close(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl._deliver(b'{"t":"tun","c":"noop"')
        ctrl._set_state(STATE_CLOSED)
        with self.assertRaises(ChannelError) as ctx:
            ctrl.recv_message(timeout=0.1)
        self.assertEqual(ctx.exception.code, 'closed')

    def test_recv_message_too_long(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl._deliver(b'a' * (CONTROL_MESSAGE_MAX_LENGTH + 1))
        with self.assertRaises(ChannelError) as ctx:
            ctrl.recv_message(timeout=0.1)
        self.assertEqual(ctx.exception.code, 'invalid')

    def test_take_send_data_waits_for_newline(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl.write(b'partial')
        data = ctrl._take_send_data(10)
        self.assertEqual(data, b'')
        ctrl.write(b'\n')
        data = ctrl._take_send_data(10)
        self.assertEqual(data, b'partial\n')

    def test_take_send_data_oversize_is_error(self):
        ctrl = ControlChannel()
        ctrl._set_state(STATE_OPEN)
        ctrl.write(b'a' * 6)
        with self.assertRaises(ChannelError) as ctx:
            ctrl._take_send_data(5)
        self.assertEqual(ctx.exception.code, 'invalid')


class ChannelManagerTests(unittest.TestCase):
    def _drain_control_messages(self, manager):
        ctrl = manager.control
        data = ctrl._take_send_data(65536)
        if not data:
            return []
        lines = data.splitlines()
        return [json.loads(line.decode('ascii')) for line in lines if line]

    def test_open_channel_sends_open(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = mgr.open_channel()
        self.assertEqual(ch.state, STATE_OPENING)
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['t'], 'ch')
        self.assertEqual(msgs[0]['c'], 'open')
        self.assertTrue(is_alice_channel(msgs[0]['ch']))

    def test_close_channel_sends_close(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        mgr._channels[1] = ch
        mgr.close_channel(1)
        self.assertEqual(ch.state, STATE_CLOSING)
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(msgs[0]['t'], 'ch')
        self.assertEqual(msgs[0]['c'], 'close')
        self.assertEqual(msgs[0]['ch'], 1)

    def test_handle_open_auto_accepts(self):
        """Channels are auto-accepted - they're generic pipes."""
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        # Receive open request from Bob (even channel ID)
        mgr.handle_control_message({
            'c': 'open',
            'ch': 2,
        })
        # Should be auto-accepted
        ch = mgr.get_channel(2)
        self.assertIsNotNone(ch)
        self.assertEqual(ch.state, STATE_OPEN)
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(msgs[0]['t'], 'ch')
        self.assertEqual(msgs[0]['c'], 'open_ok')
        self.assertEqual(msgs[0]['ch'], 2)

    def test_handle_open_rejects_wrong_ownership(self):
        """Alice should reject channels with Alice's ID (odd)."""
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        # Bob tries to open a channel with Alice's ID - should be rejected
        mgr.handle_control_message({
            'c': 'open',
            'ch': 1,  # Odd = Alice's namespace
        })
        # Should be silently ignored (no channel created, no response)
        self.assertIsNone(mgr.get_channel(1))
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(msgs, [])

    def test_handle_open_invalid_channel_id(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        mgr.handle_control_message({'c': 'open', 'ch': 'bad'})
        self.assertIsNone(mgr.get_channel('bad'))
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(msgs, [])

    def test_handle_open_ok_and_fail(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPENING)
        mgr._channels[1] = ch
        mgr.handle_control_message({'c': 'open_ok', 'ch': 1})
        self.assertEqual(ch.state, STATE_OPEN)
        ch_fail = Channel(3)
        ch_fail._set_state(STATE_OPENING)
        mgr._channels[3] = ch_fail
        mgr.handle_control_message({'c': 'open_fail', 'ch': 3,
                                    'reason': 'nope'})
        self.assertIsNone(mgr.get_channel(3))

    def test_handle_close_and_close_ok(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        mgr._channels[1] = ch
        mgr.handle_control_message({'c': 'close', 'ch': 1})
        self.assertIsNone(mgr.get_channel(1))
        msgs = self._drain_control_messages(mgr)
        self.assertEqual(msgs[0]['t'], 'ch')
        self.assertEqual(msgs[0]['c'], 'close_ok')
        ch = Channel(3)
        ch._set_state(STATE_CLOSING)
        mgr._channels[3] = ch
        mgr.handle_control_message({'c': 'close_ok', 'ch': 3})
        self.assertIsNone(mgr.get_channel(3))

    def test_deliver_segment_routes(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        mgr._channels[1] = ch
        mgr.deliver_segment(Segment(1, b'hi'))
        self.assertEqual(ch.read(2, timeout=0.1), b'hi')

    def test_collect_segments_control_priority(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ctrl = mgr.control
        ctrl.send_message({'t': 'ch', 'c': 'open', 'ch': 1, 'atype': 'ipv4',
                           'addr': '127.0.0.1', 'port': 80})
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch.write(b'abc')
        mgr._channels[1] = ch
        segments = mgr.collect_segments(128)
        self.assertTrue(segments)
        self.assertEqual(segments[0].channel, CHANNEL_CONTROL)

    def test_collect_segments_keepalive_suppressed(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch.write(b'abc')
        mgr._channels[1] = ch
        segments = mgr.collect_segments(64, keepalive_data=b'keepalive')
        channels = [seg.channel for seg in segments]
        self.assertNotIn(CHANNEL_CONTROL, channels)

    def test_collect_segments_keepalive_logging_matches_emission(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        events = []

        def fake_log_event(logger_arg, level, event, message, fields, **kwargs):
            if callable(fields):
                fields = fields()
            events.append(fields or {})

        original_log_event = channel_manager_module.log_event
        channel_manager_module.log_event = fake_log_event
        try:
            segments = mgr.collect_segments(64, keepalive_data=b'keepalive')
        finally:
            channel_manager_module.log_event = original_log_event

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].get('keepalive'))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].channel, CHANNEL_CONTROL)

        mgr = ChannelManager(is_alice=True, config=make_test_config())
        data_channel = Channel(1)
        data_channel._set_state(STATE_OPEN)
        data_channel.write(b'data')
        mgr._channels[1] = data_channel
        events = []

        channel_manager_module.log_event = fake_log_event
        try:
            segments = mgr.collect_segments(64, keepalive_data=b'keepalive')
        finally:
            channel_manager_module.log_event = original_log_event

        self.assertEqual(len(events), 1)
        self.assertFalse(events[0].get('keepalive'))
        self.assertTrue(any(seg.channel == 1 for seg in segments))
        self.assertFalse(any(seg.channel == CHANNEL_CONTROL and seg.data == b'keepalive' for seg in segments))

    def test_collect_segments_minimum_space(self):
        mgr = ChannelManager(is_alice=True, config=make_test_config())
        ch = Channel(1)
        ch._set_state(STATE_OPEN)
        ch.write(b'abc')
        mgr._channels[1] = ch
        segments = mgr.collect_segments(SEGMENT_HEADER_SIZE)
        self.assertEqual(segments, [])


if __name__ == '__main__':
    unittest.main()
