# -*- coding: ascii -*-
"""
Module loader service for dynamic module loading.

Handles 'mod' message type to load modules on demand.
This is not a regular module (doesn't inherit from BaseModule) -
it's a tunnel service that manages other modules.
"""

from __future__ import absolute_import

import logging
import threading

from .tunnel_control_messages import T_MOD, mod_load, mod_load_ok, mod_load_err
from ..compat import integer_types, to_native_str
from ..logging_util import get_logger, log_event


class ModuleLoadError(Exception):
    """Error loading a module."""
    pass


class ModuleLoader(object):
    """
    Handles module loading requests from the remote side.

    Registers with tunnel for 'mod' message type and handles:
    - load: Load a module by name from AVAILABLE_MODULES

    On the controller side (Bob), also provides load_remote() to request
    module loading on the agent side (Alice).
    """

    def __init__(self, tunnel, logger=None):
        """
        Initialize and register with tunnel.

        Args:
            tunnel: The tunnel instance to register with.
            logger: Optional logger.
        """
        self._tunnel = tunnel
        self._logger = logger or get_logger(__name__)
        self._loaded_modules = {}  # (name, module_id) -> module

        # For controller side: track pending load requests
        self._pending_lock = threading.Lock()
        self._pending = {}  # (name, module_id) -> {'waiters': [], 'in_flight': bool}

        tunnel.register_module(T_MOD, None, self._dispatch)

    @staticmethod
    def _valid_module_id(value):
        return (
            isinstance(value, integer_types) and
            not isinstance(value, bool) and
            value > 0
        )

    def _dispatch(self, msg):
        """Route incoming message to appropriate handler."""
        cmd = msg.get('c')
        if cmd == 'load':
            self._handle_load(msg)
        elif cmd == 'load_ok':
            self._handle_load_ok(msg)
        elif cmd == 'load_err':
            self._handle_load_err(msg)
        else:
            log_event(
                self._logger,
                logging.DEBUG,
                'module_loader.command_unknown',
                'Unknown module loader command',
                lambda: {'cmd': cmd},
            )

    def _handle_load(self, msg):
        """Handle module load request."""
        # Late import to avoid circular dependency
        from ..modules import AVAILABLE_MODULES

        name = msg.get('name')
        module_id = msg.get('mid')
        if not name:
            self._send(mod_load_err('', 'missing module name', module_id))
            return
        if not self._valid_module_id(module_id):
            self._send(mod_load_err(name, 'invalid module id', module_id))
            return

        key = (name, module_id)
        if key in self._loaded_modules:
            log_event(
                self._logger,
                logging.DEBUG,
                'module_loader.already_loaded',
                'Module already loaded',
                lambda: {'module': name, 'mid': module_id},
            )
            self._send(mod_load_ok(name, module_id))
            return

        module_class = AVAILABLE_MODULES.get(name)
        if module_class is None:
            log_event(
                self._logger,
                logging.WARNING,
                'module_loader.unknown',
                'Unknown module',
                lambda: {'module': name, 'mid': module_id},
            )
            self._send(mod_load_err(name, 'unknown module', module_id))
            return

        try:
            module_logger = get_logger('sfb.modules.%s' % name)
            module = module_class(self._tunnel, module_logger, module_id=module_id)
            self._loaded_modules[key] = module
            log_event(
                self._logger,
                logging.INFO,
                'module_loader.loaded',
                'Loaded module',
                lambda: {'module': name, 'mid': module_id},
            )
            self._send(mod_load_ok(name, module_id))
        except Exception as e:
            log_event(
                self._logger,
                logging.ERROR,
                'module_loader.load_failed',
                'Failed to load module',
                lambda: {
                    'module': name,
                    'mid': module_id,
                    'error': to_native_str(e),
                },
                exc_info=True,
            )
            self._send(mod_load_err(name, to_native_str(e), module_id))

    def _handle_load_ok(self, msg):
        """Handle successful load response (for controller side)."""
        name = msg.get('name')
        module_id = msg.get('mid')
        log_event(
            self._logger,
            logging.DEBUG,
            'module_loader.remote_loaded',
            'Module loaded on remote',
            lambda: {'module': name, 'mid': module_id},
        )
        self._signal_pending(name, module_id, success=True)

    def _handle_load_err(self, msg):
        """Handle failed load response (for controller side)."""
        name = msg.get('name')
        module_id = msg.get('mid')
        reason = msg.get('reason', 'unknown error')
        log_event(
            self._logger,
            logging.ERROR,
            'module_loader.remote_failed',
            'Failed to load module on remote',
            lambda: {'module': name, 'mid': module_id, 'reason': reason},
        )
        self._signal_pending(name, module_id, success=False, reason=reason)

    def _signal_pending(self, name, module_id, success, reason=None):
        """Signal a pending load request."""
        with self._pending_lock:
            pending = self._pending.get((name, module_id))
            if pending is None:
                return
            pending['in_flight'] = False
            for waiter in pending['waiters']:
                waiter['success'] = success
                waiter['reason'] = reason
                waiter['event'].set()
            if not pending['waiters']:
                self._pending.pop((name, module_id), None)

    def load_remote(self, name, module_id, timeout=30.0):
        """
        Request module loading on the remote side and wait for response.

        Args:
            name: Module name to load (e.g., 'file_transfer')
            module_id: Module instance id
            timeout: Seconds to wait for response

        Returns:
            True if module was loaded successfully

        Raises:
            ModuleLoadError: If loading failed or timed out
        """
        if not self._valid_module_id(module_id):
            raise ValueError('module_id must be a positive integer')
        waiter = {
            'event': threading.Event(),
            'success': False,
            'reason': None,
        }
        key = (name, module_id)
        send_request = False
        with self._pending_lock:
            pending = self._pending.get(key)
            if pending is None:
                pending = {
                    'waiters': [],
                    'in_flight': False,
                }
                self._pending[key] = pending
            pending['waiters'].append(waiter)
            if not pending['in_flight']:
                pending['in_flight'] = True
                send_request = True

        try:
            if send_request:
                # Send load request
                self._send(mod_load(name, module_id))
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'module_loader.send_load',
                    'Sent module load request',
                    lambda: {'module': name, 'mid': module_id},
                )

            # Wait for response
            if not waiter['event'].wait(timeout=timeout):
                raise ModuleLoadError(
                    'Timeout waiting for module load: %s/%s' % (
                        name, module_id
                    )
                )

            if not waiter['success']:
                raise ModuleLoadError('Failed to load module %s/%s: %s' % (
                    name, module_id, waiter['reason'] or 'unknown error'))

            return True
        finally:
            with self._pending_lock:
                pending = self._pending.get(key)
                if pending is None:
                    return
                if waiter in pending['waiters']:
                    pending['waiters'].remove(waiter)
                if not pending['in_flight'] and not pending['waiters']:
                    self._pending.pop(key, None)

    def _send(self, msg):
        """Send a control message."""
        self._tunnel.control.send_message(msg)

    def get_module(self, name, module_id):
        """Get a loaded module by name and module_id."""
        return self._loaded_modules.get((name, module_id))

    def shutdown(self):
        """Shutdown all loaded modules."""
        for (name, module_id), module in list(self._loaded_modules.items()):
            try:
                module.shutdown()
            except Exception:
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module_loader.shutdown_error',
                    'Error shutting down module',
                    lambda: {'module': name, 'mid': module_id},
                    exc_info=True,
                )
        self._loaded_modules.clear()
        try:
            self._tunnel.unregister_module(T_MOD, None)
        except Exception:
            pass
