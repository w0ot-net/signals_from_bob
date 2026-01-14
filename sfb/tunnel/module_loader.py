# -*- coding: ascii -*-
"""
Module loader service for dynamic module loading.

Handles 'mod' message type to load and unload modules on demand.
This is not a regular module (doesn't inherit from BaseModule) -
it's a tunnel service that manages other modules.
"""

from __future__ import absolute_import

import logging
import threading

from .tunnel_control_messages import (
    T_MOD,
    mod_load,
    mod_load_ok,
    mod_load_err,
    mod_unload,
    mod_unload_ok,
    mod_unload_err,
)
from ..compat import integer_types, to_native_str
from ..logging_util import get_logger, log_event


class ModuleLoadError(Exception):
    """Error loading a module."""
    pass


class ModuleLoader(object):
    """
    Handles module load and unload requests from the remote side.

    Registers with tunnel for 'mod' message type and handles:
    - load: Load a module by name from the module registry
    - unload: Unload a module by name and instance id

    On the controller side (Bob), also provides load_remote() to request
    module loading and unloading on the agent side (Alice).
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
        self._remote_modules = {}  # (name, module_id) -> True

        # For controller side: track pending load requests
        self._pending_lock = threading.Lock()
        self._pending = {}  # (name, module_id) -> {'waiters': [], 'in_flight': bool}
        self._pending_unload = {}  # (name, module_id) -> {'waiters': [], 'in_flight': bool}

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
        elif cmd == 'unload':
            self._handle_unload(msg)
        elif cmd == 'load_ok':
            self._handle_load_ok(msg)
        elif cmd == 'load_err':
            self._handle_load_err(msg)
        elif cmd == 'unload_ok':
            self._handle_unload_ok(msg)
        elif cmd == 'unload_err':
            self._handle_unload_err(msg)
        else:
            if self._logger.isEnabledFor(logging.DEBUG):
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
        from ..modules import get_available_module_class

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
            if self._logger.isEnabledFor(logging.DEBUG):
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'module_loader.already_loaded',
                    'Module already loaded',
                    lambda: {'module': name, 'mid': module_id},
                )
            self._send(mod_load_ok(name, module_id))
            return

        module_class = get_available_module_class(name)
        if module_class is None:
            if self._logger.isEnabledFor(logging.WARNING):
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
            if self._logger.isEnabledFor(logging.INFO):
                log_event(
                    self._logger,
                    logging.INFO,
                    'module_loader.loaded',
                    'Loaded module',
                    lambda: {'module': name, 'mid': module_id},
                )
            self._send(mod_load_ok(name, module_id))
        except Exception as e:
            if self._logger.isEnabledFor(logging.ERROR):
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

    def _handle_unload(self, msg):
        """Handle module unload request."""
        name = msg.get('name')
        module_id = msg.get('mid')
        if not name:
            self._send(mod_unload_err('', module_id, 'missing module name'))
            return
        if not self._valid_module_id(module_id):
            self._send(mod_unload_err(name, module_id, 'invalid module id'))
            return

        key = (name, module_id)
        module = self._loaded_modules.get(key)
        if module is None:
            if self._logger.isEnabledFor(logging.ERROR):
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module_loader.unload_failed',
                    'Module not loaded',
                    lambda: {'module': name, 'mid': module_id, 'reason': 'not loaded'},
                )
            self._send(mod_unload_err(name, module_id, 'not loaded'))
            return

        try:
            module.shutdown()
        except Exception as e:
            if self._logger.isEnabledFor(logging.ERROR):
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module_loader.unload_failed',
                    'Failed to unload module',
                    lambda: {
                        'module': name,
                        'mid': module_id,
                        'reason': to_native_str(e),
                    },
                    exc_info=True,
                )
            self._send(mod_unload_err(name, module_id, to_native_str(e)))
            return

        self._loaded_modules.pop(key, None)
        if self._logger.isEnabledFor(logging.INFO):
            log_event(
                self._logger,
                logging.INFO,
                'module_loader.local_unload',
                'Unloaded local module',
                lambda: {'module': name, 'mid': module_id},
            )
        self._send(mod_unload_ok(name, module_id))

    def _handle_load_ok(self, msg):
        """Handle successful load response (for controller side)."""
        name = msg.get('name')
        module_id = msg.get('mid')
        if name and self._valid_module_id(module_id):
            self._remote_modules[(name, module_id)] = True
        if self._logger.isEnabledFor(logging.DEBUG):
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
        if name and self._valid_module_id(module_id):
            self._remote_modules.pop((name, module_id), None)
        if self._logger.isEnabledFor(logging.ERROR):
            log_event(
                self._logger,
                logging.ERROR,
                'module_loader.remote_failed',
                'Failed to load module on remote',
                lambda: {'module': name, 'mid': module_id, 'reason': reason},
            )
        self._signal_pending(name, module_id, success=False, reason=reason)

    def _handle_unload_ok(self, msg):
        """Handle successful unload response (for controller side)."""
        name = msg.get('name')
        module_id = msg.get('mid')
        if name and self._valid_module_id(module_id):
            self._remote_modules.pop((name, module_id), None)
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'module_loader.remote_unload',
                'Module unloaded on remote',
                lambda: {'module': name, 'mid': module_id},
            )
        self._signal_pending(
            name,
            module_id,
            success=True,
            pending_map=self._pending_unload,
        )

    def _handle_unload_err(self, msg):
        """Handle failed unload response (for controller side)."""
        name = msg.get('name')
        module_id = msg.get('mid')
        reason = msg.get('reason', 'unknown error')
        if self._logger.isEnabledFor(logging.ERROR):
            log_event(
                self._logger,
                logging.ERROR,
                'module_loader.unload_failed',
                'Failed to unload module on remote',
                lambda: {'module': name, 'mid': module_id, 'reason': reason},
            )
        self._signal_pending(
            name,
            module_id,
            success=False,
            reason=reason,
            pending_map=self._pending_unload,
        )

    def _signal_pending(self, name, module_id, success, reason=None, pending_map=None):
        """Signal a pending request."""
        if pending_map is None:
            pending_map = self._pending
        with self._pending_lock:
            pending = pending_map.get((name, module_id))
            if pending is None:
                return
            pending['in_flight'] = False
            for waiter in pending['waiters']:
                waiter['success'] = success
                waiter['reason'] = reason
                waiter['event'].set()
            if not pending['waiters']:
                pending_map.pop((name, module_id), None)

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
                if self._logger.isEnabledFor(logging.DEBUG):
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

    def unload_remote(self, name, module_id, timeout=30.0):
        """
        Request module unload on the remote side and wait for response.

        Args:
            name: Module name to unload (e.g., 'file_transfer')
            module_id: Module instance id
            timeout: Seconds to wait for response

        Returns:
            True if module was unloaded successfully

        Raises:
            ModuleLoadError: If unloading failed or timed out
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
            pending = self._pending_unload.get(key)
            if pending is None:
                pending = {
                    'waiters': [],
                    'in_flight': False,
                }
                self._pending_unload[key] = pending
            pending['waiters'].append(waiter)
            if not pending['in_flight']:
                pending['in_flight'] = True
                send_request = True

        try:
            if send_request:
                self._send(mod_unload(name, module_id))

            if not waiter['event'].wait(timeout=timeout):
                if self._logger.isEnabledFor(logging.ERROR):
                    log_event(
                        self._logger,
                        logging.ERROR,
                        'module_loader.unload_failed',
                        'Timeout waiting for module unload',
                        lambda: {
                            'module': name,
                            'mid': module_id,
                            'reason': 'timeout',
                        },
                    )
                raise ModuleLoadError(
                    'Timeout waiting for module unload: %s/%s' % (
                        name, module_id
                    )
                )

            if not waiter['success']:
                raise ModuleLoadError('Failed to unload module %s/%s: %s' % (
                    name, module_id, waiter['reason'] or 'unknown error'))

            return True
        finally:
            with self._pending_lock:
                pending = self._pending_unload.get(key)
                if pending is None:
                    return
                if waiter in pending['waiters']:
                    pending['waiters'].remove(waiter)
                if not pending['in_flight'] and not pending['waiters']:
                    self._pending_unload.pop(key, None)

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
                if self._logger.isEnabledFor(logging.ERROR):
                    log_event(
                        self._logger,
                        logging.ERROR,
                        'module_loader.shutdown_error',
                        'Error shutting down module',
                        lambda: {'module': name, 'mid': module_id},
                        exc_info=True,
                    )
        self._loaded_modules.clear()
        self._remote_modules.clear()
        try:
            self._tunnel.unregister_module(T_MOD, None)
        except Exception:
            pass
