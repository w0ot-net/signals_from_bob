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
from ..compat import to_native_str
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
        self._loaded_modules = {}

        # For controller side: track pending load requests
        self._pending_lock = threading.Lock()
        self._pending = {}  # name -> Event

        tunnel.register_module(T_MOD, self._dispatch)

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
        if not name:
            self._send(mod_load_err('', 'missing module name'))
            return

        if name in self._loaded_modules:
            log_event(
                self._logger,
                logging.DEBUG,
                'module_loader.already_loaded',
                'Module already loaded',
                lambda: {'module': name},
            )
            self._send(mod_load_ok(name))
            return

        module_class = AVAILABLE_MODULES.get(name)
        if module_class is None:
            log_event(
                self._logger,
                logging.WARNING,
                'module_loader.unknown',
                'Unknown module',
                lambda: {'module': name},
            )
            self._send(mod_load_err(name, 'unknown module'))
            return

        try:
            module_logger = get_logger('sfb.modules.%s' % name)
            module = module_class(self._tunnel, module_logger)
            self._loaded_modules[name] = module
            log_event(
                self._logger,
                logging.INFO,
                'module_loader.loaded',
                'Loaded module',
                lambda: {'module': name},
            )
            self._send(mod_load_ok(name))
        except Exception as e:
            log_event(
                self._logger,
                logging.ERROR,
                'module_loader.load_failed',
                'Failed to load module',
                lambda: {'module': name, 'error': to_native_str(e)},
                exc_info=True,
            )
            self._send(mod_load_err(name, to_native_str(e)))

    def _handle_load_ok(self, msg):
        """Handle successful load response (for controller side)."""
        name = msg.get('name')
        log_event(
            self._logger,
            logging.DEBUG,
            'module_loader.remote_loaded',
            'Module loaded on remote',
            lambda: {'module': name},
        )
        self._signal_pending(name, success=True)

    def _handle_load_err(self, msg):
        """Handle failed load response (for controller side)."""
        name = msg.get('name')
        reason = msg.get('reason', 'unknown error')
        log_event(
            self._logger,
            logging.ERROR,
            'module_loader.remote_failed',
            'Failed to load module on remote',
            lambda: {'module': name, 'reason': reason},
        )
        self._signal_pending(name, success=False, reason=reason)

    def _signal_pending(self, name, success, reason=None):
        """Signal a pending load request."""
        with self._pending_lock:
            pending = self._pending.get(name)
            if pending:
                pending['success'] = success
                pending['reason'] = reason
                pending['event'].set()

    def load_remote(self, name, timeout=30.0):
        """
        Request module loading on the remote side and wait for response.

        Args:
            name: Module name to load (e.g., 'file_transfer')
            timeout: Seconds to wait for response

        Returns:
            True if module was loaded successfully

        Raises:
            ModuleLoadError: If loading failed or timed out
        """
        # Create pending entry
        created = False
        with self._pending_lock:
            pending = self._pending.get(name)
            if pending is None:
                pending = {
                    'event': threading.Event(),
                    'success': False,
                    'reason': None,
                }
                self._pending[name] = pending
                created = True

        try:
            if created:
                # Send load request
                self._send(mod_load(name))
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'module_loader.send_load',
                    'Sent module load request',
                    lambda: {'module': name},
                )

            # Wait for response
            if not pending['event'].wait(timeout=timeout):
                raise ModuleLoadError('Timeout waiting for module load: %s' % name)

            if not pending['success']:
                raise ModuleLoadError('Failed to load module %s: %s' % (
                    name, pending['reason'] or 'unknown error'))

            return True
        finally:
            if created:
                with self._pending_lock:
                    self._pending.pop(name, None)

    def _send(self, msg):
        """Send a control message."""
        self._tunnel.control.send_message(msg)

    def get_module(self, name):
        """Get a loaded module by name."""
        return self._loaded_modules.get(name)

    def shutdown(self):
        """Shutdown all loaded modules."""
        for name, module in list(self._loaded_modules.items()):
            try:
                module.shutdown()
            except Exception:
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module_loader.shutdown_error',
                    'Error shutting down module',
                    lambda: {'module': name},
                    exc_info=True,
                )
        self._loaded_modules.clear()
        try:
            self._tunnel.unregister_module(T_MOD)
        except Exception:
            pass
