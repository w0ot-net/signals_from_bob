# -*- coding: ascii -*-
"""Tests for module loader."""

from __future__ import absolute_import

import threading
import unittest

import sfb.modules as modules
from sfb.compat import to_native_str
from sfb.tunnel.module_loader import ModuleLoadError, ModuleLoader
from sfb.tunnel.tunnel_control_messages import (
    T_MOD,
    mod_load,
    mod_load_err,
    mod_load_ok,
)


class DummyControl(object):
    def __init__(self):
        self.sent_messages = []
        self.sent_event = threading.Event()

    def send_message(self, msg):
        self.sent_messages.append(msg)
        self.sent_event.set()

    def wait_for_send(self, timeout):
        return self.sent_event.wait(timeout)

    def clear(self):
        self.sent_event.clear()


class DummyTunnel(object):
    def __init__(self):
        self.control = DummyControl()
        self.registered = {}
        self.unregistered = []

    def register_module(self, msg_type, handler):
        self.registered[msg_type] = handler

    def unregister_module(self, msg_type):
        self.unregistered.append(msg_type)
        self.registered.pop(msg_type, None)


class DummyModule(object):
    created = 0
    shutdown_called = 0

    def __init__(self, tunnel, logger):
        DummyModule.created += 1
        self._tunnel = tunnel
        self._logger = logger

    def shutdown(self):
        DummyModule.shutdown_called += 1


class ExplodingModule(object):
    def __init__(self, tunnel, logger):
        raise ValueError('boom')


class ModuleLoaderTest(unittest.TestCase):
    def setUp(self):
        DummyModule.created = 0
        DummyModule.shutdown_called = 0

    def _patch_modules(self, mapping):
        original = modules.AVAILABLE_MODULES
        modules.AVAILABLE_MODULES = mapping
        self.addCleanup(setattr, modules, 'AVAILABLE_MODULES', original)

    def _last_msg(self, control):
        self.assertTrue(control.sent_messages)
        return control.sent_messages[-1].to_dict()

    def test_handle_load_missing_name_sends_error(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        loader._handle_load({})
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load_err('', 'missing module name').to_dict(),
        )

    def test_handle_load_unknown_module_sends_error(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        loader._handle_load({'name': 'nope'})
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load_err('nope', 'unknown module').to_dict(),
        )

    def test_handle_load_success_and_already_loaded(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        self._patch_modules({'dummy': DummyModule})
        loader._handle_load({'name': 'dummy'})
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load_ok('dummy').to_dict(),
        )
        module = loader.get_module('dummy')
        self.assertIsNotNone(module)
        self.assertEqual(DummyModule.created, 1)

        tunnel.control.clear()
        loader._handle_load({'name': 'dummy'})
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load_ok('dummy').to_dict(),
        )
        self.assertEqual(DummyModule.created, 1)
        self.assertIs(loader.get_module('dummy'), module)

    def test_handle_load_exception_sends_error(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        self._patch_modules({'boom': ExplodingModule})
        loader._handle_load({'name': 'boom'})
        expected_reason = to_native_str(ValueError('boom'))
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load_err('boom', expected_reason).to_dict(),
        )
        self.assertIsNone(loader.get_module('boom'))

    def test_load_remote_success(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        outcome = {'result': None, 'error': None}

        def runner():
            try:
                outcome['result'] = loader.load_remote('dummy', timeout=1.0)
            except Exception as exc:
                outcome['error'] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        self.assertTrue(tunnel.control.wait_for_send(0.5))
        self.assertEqual(
            self._last_msg(tunnel.control),
            mod_load('dummy').to_dict(),
        )
        loader._handle_load_ok({'name': 'dummy'})
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome['result'], True)
        self.assertIsNone(outcome['error'])
        self.assertEqual(loader._pending, {})

    def test_load_remote_error(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        outcome = {'error': None}

        def runner():
            try:
                loader.load_remote('dummy', timeout=1.0)
            except Exception as exc:
                outcome['error'] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        self.assertTrue(tunnel.control.wait_for_send(0.5))
        loader._handle_load_err({'name': 'dummy', 'reason': 'nope'})
        thread.join(1.0)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome['error'], ModuleLoadError)
        self.assertIn('nope', to_native_str(outcome['error']))
        self.assertEqual(loader._pending, {})

    def test_load_remote_timeout(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        with self.assertRaises(ModuleLoadError):
            loader.load_remote('dummy', timeout=0.1)
        self.assertEqual(loader._pending, {})

    def test_shutdown_calls_module_and_unregisters(self):
        tunnel = DummyTunnel()
        loader = ModuleLoader(tunnel)
        self._patch_modules({'dummy': DummyModule})
        loader._handle_load({'name': 'dummy'})
        loader.shutdown()
        self.assertEqual(DummyModule.shutdown_called, 1)
        self.assertIsNone(loader.get_module('dummy'))
        self.assertIn(T_MOD, tunnel.unregistered)


if __name__ == '__main__':
    unittest.main()
