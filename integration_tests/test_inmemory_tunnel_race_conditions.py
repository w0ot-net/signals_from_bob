# -*- coding: ascii -*-
"""Integration tests for tunnel race condition fixes."""

from __future__ import absolute_import

import threading
import time
import unittest

from sfb.config import Config
from sfb.transport import create_inmemory_transport_pair
from sfb.tunnel.alice_tunnel import AliceTunnel
from sfb.tunnel.bob_tunnel import BobTunnel
from sfb.tunnel.module_loader import ModuleLoadError
from sfb.tunnel.tunnel_control_messages import T_MOD, mod_load_ok


class ModLoadResponder(object):
    def __init__(self, tunnel, delay_sec):
        self._tunnel = tunnel
        self._delay_sec = delay_sec
        self._count = 0
        self._lock = threading.Lock()

    @property
    def load_count(self):
        with self._lock:
            return self._count

    def handle(self, msg):
        if msg.get('c') != 'load':
            return
        name = msg.get('name')
        with self._lock:
            self._count += 1
        thread = threading.Thread(target=self._send_response, args=(name,))
        thread.daemon = True
        thread.start()

    def _send_response(self, name):
        if self._delay_sec:
            time.sleep(self._delay_sec)
        self._tunnel.control.send_message(mod_load_ok(name))


class TunnelRaceIntegrationTests(unittest.TestCase):
    def _make_config(self):
        config = Config()
        config.tunnel_connect_timeout = 2.0
        config.tunnel_bob_poll_interval = 0.05
        config.tunnel_bg_stop_timeout = 1.0
        config.tunnel_keepalive_interval = 0.1
        config.non_blocking_poll_timeout = 0.001
        config.tunnel_tick_sleep = 0.0005
        return config

    def _build_tunnels(self):
        config = self._make_config()
        client, server = create_inmemory_transport_pair(config)
        alice = AliceTunnel(client, config)
        bob = BobTunnel(server, config)
        return alice, bob

    def _disable_module_loader(self, tunnel):
        loader = tunnel.module_loader
        if loader is None:
            return
        loader.shutdown()
        tunnel._module_loader = None

    def test_concurrent_load_remote_timeout_does_not_block_other_waiter(self):
        alice = None
        bob = None
        try:
            alice, bob = self._build_tunnels()
            self._disable_module_loader(alice)
            responder = ModLoadResponder(alice, delay_sec=0.4)
            alice.register_module(T_MOD, responder.handle)

            bob_loader = bob.enable_module_loader()

            bob.start_background()
            bob_thread = bob._bg_thread
            bob.start_background()
            self.assertIs(bob._bg_thread, bob_thread)
            self.assertTrue(bob._bg_thread.is_alive())

            alice.connect(timeout=1.0)
            alice.start_background()
            alice_thread = alice._bg_thread
            alice.start_background()
            self.assertIs(alice._bg_thread, alice_thread)
            self.assertTrue(alice._bg_thread.is_alive())

            time.sleep(0.05)

            results = [None, None]
            errors = [None, None]

            def runner(index, timeout):
                try:
                    results[index] = bob_loader.load_remote(
                        'dummy', timeout=timeout
                    )
                except Exception as exc:
                    errors[index] = exc

            thread_short = threading.Thread(target=runner, args=(0, 0.2))
            thread_long = threading.Thread(target=runner, args=(1, 2.0))
            thread_short.start()
            thread_long.start()

            thread_short.join(3.0)
            thread_long.join(3.0)

            self.assertFalse(thread_short.is_alive())
            self.assertFalse(thread_long.is_alive())
            self.assertIsInstance(errors[0], ModuleLoadError)
            self.assertIsNone(errors[1])
            self.assertEqual(results[1], True)

            self.assertEqual(responder.load_count, 1)
            self.assertEqual(bob_loader._pending, {})
        finally:
            if alice is not None:
                alice.stop_background()
                alice.close()
            if bob is not None:
                bob.stop_background()
                bob.close()


if __name__ == '__main__':
    unittest.main()
