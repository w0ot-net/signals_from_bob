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
        self._logger = logger or logging.getLogger('ModuleLoader')
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
            self._logger.debug('Unknown mod command: %s', cmd)

    def _handle_load(self, msg):
        """Handle module load request."""
        # Late import to avoid circular dependency
        from ..modules import AVAILABLE_MODULES

        name = msg.get('name')
        if not name:
            self._send(mod_load_err('', 'missing module name'))
            return

        if name in self._loaded_modules:
            self._logger.debug('Module already loaded: %s', name)
            self._send(mod_load_ok(name))
            return

        module_class = AVAILABLE_MODULES.get(name)
        if module_class is None:
            self._logger.warning('Unknown module: %s', name)
            self._send(mod_load_err(name, 'unknown module'))
            return

        try:
            module = module_class(self._tunnel, self._logger)
            self._loaded_modules[name] = module
            self._logger.info('Loaded module: %s', name)
            self._send(mod_load_ok(name))
        except Exception as e:
            self._logger.exception('Failed to load module %s: %s', name, e)
            self._send(mod_load_err(name, str(e)))

    def _handle_load_ok(self, msg):
        """Handle successful load response (for controller side)."""
        name = msg.get('name')
        self._logger.debug('Module loaded on remote: %s', name)
        self._signal_pending(name, success=True)

    def _handle_load_err(self, msg):
        """Handle failed load response (for controller side)."""
        name = msg.get('name')
        reason = msg.get('reason', 'unknown error')
        self._logger.error('Failed to load module %s on remote: %s', name, reason)
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
        pending = {
            'event': threading.Event(),
            'success': False,
            'reason': None,
        }
        with self._pending_lock:
            self._pending[name] = pending

        try:
            # Send load request
            self._send(mod_load(name))
            self._logger.debug('Sent mod:load for %s', name)

            # Wait for response
            if not pending['event'].wait(timeout=timeout):
                raise ModuleLoadError('Timeout waiting for module load: %s' % name)

            if not pending['success']:
                raise ModuleLoadError('Failed to load module %s: %s' % (
                    name, pending['reason'] or 'unknown error'))

            return True
        finally:
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
                self._logger.exception('Error shutting down module %s', name)
        self._loaded_modules.clear()
        try:
            self._tunnel.unregister_module(T_MOD)
        except Exception:
            pass
