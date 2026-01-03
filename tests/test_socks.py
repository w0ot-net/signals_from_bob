# -*- coding: ascii -*-
from __future__ import absolute_import

import errno
import logging
import socket
import struct
import threading
import unittest

from sfb.config import Config
from sfb.channel import Channel, STATE_CLOSED, STATE_OPEN
from sfb.modules.socks import socks_server
from sfb.modules.socks import socks_relay
from sfb.modules.socks import data_pump
from sfb.modules.socks import relay_connection
from sfb.modules.socks.socks_server import SocksServerModule
from sfb.modules.socks.relay_connection import RelayConnection
from sfb import time_provider


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
        self.channel_manager = None

    def register_module(self, module_type, handler):
        self._modules[module_type] = handler

    def unregister_module(self, module_type):
        self._modules.pop(module_type, None)


class DummyChannelManager(object):
    def __init__(self, channel):
        self._channel = channel

    def open_channel(self):
        self._channel._set_state(STATE_OPEN)
        return self._channel

    def get_channel(self, ch_id):
        if self._channel.id == ch_id:
            return self._channel
        return None


def capture_log_events(module, events):
    original = module.log_event

    def fake_log_event(logger, level, event, message, fields, **kwargs):
        events.append((event, fields()))

    module.log_event = fake_log_event
    return original


class FakeServerSocket(object):
    def __init__(self):
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def accept(self):
        raise socket.error('boom')


def make_socket_pair():
    listener = None
    client = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(listener.getsockname())
        server, _ = listener.accept()
        listener.close()
        return client, server
    except OSError as exc:
        if exc.errno in (errno.EPERM, errno.EACCES):
            raise unittest.SkipTest('socket creation not permitted')
        raise
    finally:
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


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

        original_sleep = socks_server.time_provider.sleep
        socks_server.time_provider.sleep = fake_sleep
        try:
            module._accept_loop()
        finally:
            socks_server.time_provider.sleep = original_sleep
            module._running = False

        self.assertTrue(sleep_calls)
        self.assertGreaterEqual(sleep_calls[0], config.non_blocking_poll_timeout)
        if len(sleep_calls) > 1:
            self.assertGreaterEqual(sleep_calls[1], sleep_calls[0])
            self.assertLessEqual(sleep_calls[1], config.socks_accept_timeout)

    def test_pump_socket_to_channel_waits_for_send_space(self):
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            socks_pump_backoff_max=0.01,
            socks_relay_buffer_size=4,
        )
        channel = Channel(1, max_send_buf=4)
        channel._set_state(STATE_OPEN)
        sock, peer = make_socket_pair()
        stop_event = threading.Event()
        wait_calls = []

        original_wait = channel.wait_send_space

        def wait_send_space(timeout=None):
            wait_calls.append(timeout)
            return original_wait(timeout=timeout)

        channel.wait_send_space = wait_send_space
        try:
            t = threading.Thread(
                target=data_pump.pump_socket_to_channel,
                args=(
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
                ),
            )
            t.daemon = True
            t.start()
            payload = b'abcdefghij'
            peer.sendall(payload)
            time_provider.sleep(0.05)
            drained = b''
            deadline = time_provider.now() + 1.0
            while len(drained) < len(payload) and time_provider.now() < deadline:
                chunk = channel._take_send_data(16)
                if chunk:
                    drained += chunk
                else:
                    time_provider.sleep(0.01)
            try:
                peer.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            peer.close()
            t.join(timeout=1.0)
            self.assertFalse(t.is_alive())
            self.assertEqual(drained, payload)
            self.assertTrue(wait_calls)
            self.assertFalse(stop_event.is_set())
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def test_pump_channel_to_socket_stop_event_exits(self):
        config = Config(
            dns_base_domain='test.local',
            channel_max_recv_buf=262144,
            non_blocking_poll_timeout=0.001,
            socks_pump_backoff_max=0.01,
            socks_relay_buffer_size=4096,
            socks_relay_channel_timeout=0.01,
        )
        channel = Channel(1, max_recv_buf=config.channel_max_recv_buf)
        channel._set_state(STATE_OPEN)
        sock, peer = make_socket_pair()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        except Exception:
            pass
        stop_event = threading.Event()
        channel._deliver(b'a' * 65536)
        try:
            t = threading.Thread(
                target=data_pump.pump_channel_to_socket,
                args=(
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
                ),
            )
            t.daemon = True
            t.start()
            time_provider.sleep(0.05)
            stop_event.set()
            t.join(timeout=1.0)
            self.assertFalse(t.is_alive())
        finally:
            try:
                peer.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def test_pump_channel_to_socket_eof_flushes_pending(self):
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            socks_pump_backoff_max=0.01,
            socks_relay_buffer_size=1024,
            socks_relay_channel_timeout=0.01,
        )
        channel = Channel(1, max_recv_buf=1024)
        channel._set_state(STATE_OPEN)
        sock, peer = make_socket_pair()
        stop_event = threading.Event()
        channel._deliver(b'hello')
        channel._set_state(STATE_CLOSED)
        try:
            t = threading.Thread(
                target=data_pump.pump_channel_to_socket,
                args=(
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
                ),
            )
            t.daemon = True
            t.start()
            peer.settimeout(1.0)
            received = b''
            while True:
                try:
                    chunk = peer.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received += chunk
            t.join(timeout=1.0)
            self.assertFalse(t.is_alive())
            self.assertEqual(received, b'hello')
            self.assertFalse(stop_event.is_set())
        finally:
            try:
                peer.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def test_pump_channel_to_socket_backpressure_drains_recv_buf(self):
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            protocol_max_packet_size=64,
            socks_pump_backoff_max=0.01,
            socks_relay_buffer_size=32,
            socks_relay_channel_timeout=0.01,
            max_in_flight=2,
        )
        channel = Channel(1, max_recv_buf=128)
        channel._set_state(STATE_OPEN)
        sock, peer = make_socket_pair()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512)
        except Exception:
            pass
        stop_event = threading.Event()
        try:
            t = threading.Thread(
                target=data_pump.pump_channel_to_socket,
                args=(
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
                ),
            )
            t.daemon = True
            t.start()
            time_provider.sleep(0.02)
            chunks = [b'x' * 16] * 8
            for chunk in chunks:
                channel._deliver(chunk)
                deadline = time_provider.now() + 0.2
                while channel.recv_buf_size and time_provider.now() < deadline:
                    time_provider.sleep(0.005)
            stop_event.set()
            t.join(timeout=1.0)
            self.assertFalse(t.is_alive())
        finally:
            try:
                peer.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass


class SocksInstrumentationTests(unittest.TestCase):
    def test_server_handshake_logs_event(self):
        events = []
        original_log = capture_log_events(socks_server, events)
        original_relay = socks_server.RelayConnection

        class DummyRelayConnection(object):
            def __init__(self, rid, ch, channel, sock, logger, config, side,
                         peer_label, socket_to_channel_label,
                         channel_to_socket_label, thread_names=None):
                self.rid = rid
                self.ch = ch

            def start_relay(self):
                return None

            def wait(self, timeout=None):
                return None

            def stop(self):
                return None

        try:
            socks_server.RelayConnection = DummyRelayConnection
            config = Config(
                dns_base_domain='test.local',
                socks_channel_open_timeout=0.1,
                socks_connect_timeout=0.1,
            )
            tunnel = DummyTunnel(config)
            channel = Channel(2, max_send_buf=4096, max_recv_buf=4096)
            channel._set_state(STATE_OPEN)
            tunnel.channel_manager = DummyChannelManager(channel)
            module = SocksServerModule(tunnel, logger=make_test_logger())

            def fake_send_message(msg):
                if hasattr(msg, 'to_dict'):
                    msg = msg.to_dict()
                if msg.get('c') == 'connect':
                    module.handle_connect_ok({
                        'rid': msg.get('rid'),
                        'ch': msg.get('ch'),
                        'bhost': '127.0.0.1',
                        'bport': 8080,
                    })
                return msg

            module.send_message = fake_send_message
            client, server = make_socket_pair()
            try:
                client.settimeout(1.0)
                host = b'example.com'
                client.sendall(b'\x05\x01\x00')
                request = b'\x05\x01\x00\x03' + struct.pack('!B', len(host))
                request += host + struct.pack('!H', 80)
                client.sendall(request)
                module._handle_client(server, ('127.0.0.1', 5555))
                try:
                    client.recv(2)
                    client.recv(10)
                except socket.timeout:
                    pass
            finally:
                try:
                    server.close()
                except Exception:
                    pass
                try:
                    client.close()
                except Exception:
                    pass
        finally:
            socks_server.log_event = original_log
            socks_server.RelayConnection = original_relay

        event_names = [entry[0] for entry in events]
        self.assertIn('sock.server_handshake', event_names)
        self.assertIn('sock.connect_send', event_names)

    def test_relay_connect_error_logs_event(self):
        events = []
        original_log = capture_log_events(socks_relay, events)
        try:
            config = Config(dns_base_domain='test.local')
            tunnel = DummyTunnel(config)
            channel = Channel(2, max_send_buf=4096, max_recv_buf=4096)
            channel._set_state(STATE_OPEN)
            tunnel.channel_manager = DummyChannelManager(channel)
            module = socks_relay.SocksRelayModule(tunnel, logger=make_test_logger())

            def fake_send_message(msg):
                return msg

            def refused(host, port, timeout=None):
                raise socket.error(errno.ECONNREFUSED, 'refused')

            module.send_message = fake_send_message
            module._connect_target = refused
            module.handle_connect({
                'rid': 1,
                'ch': channel.id,
                'host': 'example.com',
                'port': 80,
            })
        finally:
            socks_relay.log_event = original_log

        event_names = [entry[0] for entry in events]
        self.assertIn('sock.connect_err_send', event_names)
        self.assertIn('sock.relay_target_connect', event_names)

    def test_relay_stop_logs_event(self):
        events = []
        original_log = capture_log_events(relay_connection, events)
        config = Config(
            dns_base_domain='test.local',
            non_blocking_poll_timeout=0.001,
            socks_pump_backoff_max=0.01,
            socks_relay_buffer_size=256,
            socks_relay_channel_timeout=0.01,
        )
        channel = Channel(1, max_send_buf=4096, max_recv_buf=4096)
        channel._set_state(STATE_OPEN)
        sock, peer = make_socket_pair()
        try:
            conn = RelayConnection(
                1,
                channel.id,
                channel,
                sock,
                make_test_logger(),
                config,
                side='bob',
                peer_label='Client',
                socket_to_channel_label='client_to_channel',
                channel_to_socket_label='channel_to_client',
            )
            conn.start_relay()
            time_provider.sleep(0.05)
            conn.stop()
        finally:
            relay_connection.log_event = original_log
            try:
                peer.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

        event_names = [entry[0] for entry in events]
        self.assertIn('sock.relay_stop', event_names)


if __name__ == '__main__':
    unittest.main()
