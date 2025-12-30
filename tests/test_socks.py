# -*- coding: ascii -*-
from __future__ import absolute_import

import logging
import socket
import threading
import unittest

from sfb.config import Config
from sfb.channel import ChannelError
from sfb.modules.socks import socks_server
from sfb.modules.socks import data_pump
from sfb.modules.socks.socks_server import SocksServerModule


def make_test_logger():
    logger = logging.getLogger('socks-test.%s' % id(object()))
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


class DummyControl(object):
    def send_message(self, msg):
        return msg


class DummyTunnel(object):
    def __init__(self, config):
        self._config = config
        self.control = DummyControl()
        self._modules = {}

    def register_module(self, module_type, handler):
        self._modules[module_type] = handler

    def unregister_module(self, module_type):
        self._modules.pop(module_type, None)


class FakeServerSocket(object):
    def __init__(self):
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def accept(self):
        raise socket.error('boom')


class FakeRecvSocket(object):
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        if self._payloads:
            return self._payloads.pop(0)
        return b''


class BufferFullChannel(object):
    def __init__(self, failures):
        self._failures = failures
        self.send_buf_size = 0

    def write(self, data):
        if self._failures > 0:
            self._failures -= 1
            raise ChannelError('buffer_full', 'Send buffer full')
        return len(data)


class FakeReadChannel(object):
    def __init__(self, reads):
        self._reads = list(reads)
        self.recv_buf_size = 0

    def read(self, size, timeout=None):
        if self._reads:
            return self._reads.pop(0)
        return b''


class FakeSendSocket(object):
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


class SocksLoopTests(unittest.TestCase):
    def test_accept_loop_backoff_uses_non_blocking_timeout(self):
        config = Config(
            dns_base_domain='test.local',
            socks_accept_timeout=0.01,
            non_blocking_poll_timeout=0.001,
        )
        tunnel = DummyTunnel(config)
        module = SocksServerModule(tunnel, logger=make_test_logger())
        module._server_socket = FakeServerSocket()
        module._running = True

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                module._running = False

        original_sleep = socks_server.time.sleep
        socks_server.time.sleep = fake_sleep
        try:
            module._accept_loop()
        finally:
            socks_server.time.sleep = original_sleep
            module._running = False

        self.assertTrue(sleep_calls)
        self.assertGreaterEqual(sleep_calls[0], config.non_blocking_poll_timeout)
        if len(sleep_calls) > 1:
            self.assertGreaterEqual(sleep_calls[1], sleep_calls[0])
            self.assertLessEqual(sleep_calls[1], config.socks_accept_timeout)

    def test_pump_socket_to_channel_backoff_uses_non_blocking_timeout(self):
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            socks_relay_socket_timeout=0.01,
            socks_relay_buffer_size=1024,
        )
        sock = FakeRecvSocket([b'abc', b''])
        channel = BufferFullChannel(failures=2)
        stop_event = threading.Event()

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        original_sleep = data_pump.time.sleep
        data_pump.time.sleep = fake_sleep
        try:
            data_pump.pump_socket_to_channel(
                sock,
                channel,
                config,
                make_test_logger(),
                stop_event,
                1,
                1,
                'bob',
                'Client',
                'client_to_channel',
            )
        finally:
            data_pump.time.sleep = original_sleep

        self.assertGreaterEqual(len(sleep_calls), 2)
        self.assertGreaterEqual(sleep_calls[0], config.non_blocking_poll_timeout)
        self.assertGreaterEqual(sleep_calls[1], sleep_calls[0])

    def test_pump_channel_to_socket_timeout_sleeps(self):
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            socks_relay_channel_timeout=0.001,
            socks_relay_buffer_size=1024,
        )
        channel = FakeReadChannel([None, b''])
        sock = FakeSendSocket()
        stop_event = threading.Event()

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        original_sleep = data_pump.time.sleep
        data_pump.time.sleep = fake_sleep
        try:
            data_pump.pump_channel_to_socket(
                channel,
                sock,
                config,
                make_test_logger(),
                stop_event,
                1,
                1,
                'bob',
                'Client',
                'channel_to_client',
            )
        finally:
            data_pump.time.sleep = original_sleep

        self.assertTrue(sleep_calls)
        self.assertGreaterEqual(sleep_calls[0], config.non_blocking_poll_timeout)


if __name__ == '__main__':
    unittest.main()
