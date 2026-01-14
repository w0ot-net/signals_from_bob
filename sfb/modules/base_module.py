# -*- coding: ascii -*-
"""
Base module infrastructure for tunnel modules.

Provides common functionality for all modules:
- Registration with tunnel
- Message dispatch to handle_X methods
- Threading for blocking handlers
- Request-response correlation
"""

from __future__ import absolute_import

import logging
import threading

from ..compat import integer_types
from ..logging_util import get_logger, log_event


class ModuleError(Exception):
    """Base exception for module errors."""

    def __init__(self, code, reason=None):
        Exception.__init__(self, reason or code)
        self.code = code
        self.reason = reason or code


def invalid_spec(reason, error_class=None):
    if error_class is None:
        error_class = ModuleError
    return error_class('invalid_spec', reason)


class _PendingRequest(object):
    """Tracks a pending request awaiting response."""

    __slots__ = ('event', 'response')

    def __init__(self):
        self.event = threading.Event()
        self.response = None


def blocking(func):
    """
    Decorator: run handler in separate thread.

    Use this for handlers that perform blocking I/O (file operations,
    network calls, etc.) to avoid blocking the tunnel's message loop.

    Example:
        @blocking
        def handle_get(self, msg):
            # This runs in its own thread
            self._send_file(msg)
    """
    func._blocking = True
    return func


class BaseModule(object):
    """
    Base class for tunnel modules.

    Subclasses must:
    - Set TYPE class attribute to their message type (e.g., 'file', 'sock')
    - Implement handle_X methods for each command they handle

    Example:
        class MyModule(BaseModule):
            TYPE = 'mymod'

            def handle_start(self, msg):
                # Handle {"t":"mymod","c":"start",...}
                pass

            @blocking
            def handle_work(self, msg):
                # Blocking handler runs in thread
                do_slow_io()
    """

    TYPE = None  # Subclass must override
    DEFAULT_COMMAND = None
    REQUIRES_COMMAND = False
    REMOTE_MODULE = None
    USES_SUBCOMMANDS = True

    @classmethod
    def register_commands(cls, subparsers, role, config=None):
        """
        Register CLI subcommands for this module.

        Override in subclass to add argparse subcommands.

        Args:
            subparsers: argparse subparsers object to add commands to.
            role: 'client' or 'server' - determines which commands to register.
            config: Optional Config instance for defaults.
        """
        pass

    @classmethod
    def run_command(cls, args, tunnel, logger):
        """
        Execute a CLI command for this module.

        Override in subclass to implement command execution. This is called
        after the tunnel is connected and the module is loaded on the peer.

        Args:
            args: Parsed argparse namespace with command arguments.
            tunnel: Connected tunnel instance.
            logger: Logger for output.

        Returns:
            Exit code (0 for success).
        """
        if logger.isEnabledFor(logging.WARNING):
            log_event(
                logger,
                logging.WARNING,
                'module.command_missing',
                'Module does not implement run_command',
                lambda: {'module': cls.__name__},
            )
        return 1

    def __init__(self, tunnel, logger=None, module_id=1):
        """
        Initialize module and register with tunnel.

        Args:
            tunnel: The tunnel instance to register with.
            logger: Optional logger. Defaults to module class name.
            module_id: Module instance id (positive integer).
        """
        if self.TYPE is None:
            raise ValueError('Subclass must define TYPE')
        if (not isinstance(module_id, integer_types) or
                isinstance(module_id, bool) or module_id <= 0):
            raise ValueError('module_id must be a positive integer')

        self._tunnel = tunnel
        self._logger = logger or get_logger(self.__class__.__module__)
        self._module_id = module_id
        self._threads = []
        self._threads_lock = threading.Lock()
        self._shutdown = False

        tunnel.register_module(self.TYPE, module_id, self._dispatch)
        self._pending_lock = threading.Lock()

    @property
    def module_id(self):
        return self._module_id

    def shutdown(self):
        """
        Stop module and wait for handler threads to complete.

        Call this before destroying the module to ensure clean shutdown.
        """
        self._shutdown = True
        try:
            self.unregister()
        except Exception:
            if self._logger.isEnabledFor(logging.ERROR):
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module.unregister_failed',
                    'Failed to unregister module',
                    lambda: {'type': self.TYPE, 'mid': self._module_id},
                    exc_info=True,
                )
        with self._threads_lock:
            threads = list(self._threads)
        timeout = getattr(self._tunnel._config, 'module_shutdown_timeout', 5.0)
        for t in threads:
            t.join(timeout=timeout)

    def unregister(self):
        """Unregister from tunnel."""
        self._tunnel.unregister_module(self.TYPE, self._module_id)

    def send_message(self, msg):
        """
        Send a control message via the tunnel.

        Args:
            msg: Dict or ControlMessage to send on channel 0.
        """
        if hasattr(msg, 'to_dict'):
            msg = msg.to_dict()
        elif isinstance(msg, dict):
            msg = dict(msg)
        else:
            raise ValueError('msg must be dict or ControlMessage')
        msg_mid = msg.get('mid')
        if msg_mid is None:
            msg['mid'] = self._module_id
        elif msg_mid != self._module_id:
            raise ValueError('message mid mismatch: %s != %s' % (
                msg_mid, self._module_id
            ))
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'module.send',
                'Module send',
                lambda: {'type': self.TYPE, 'mid': self._module_id, 'msg': msg},
            )
        self._tunnel.control.send_message(msg)

    def _dispatch(self, msg):
        """
        Route incoming message to appropriate handle_X method.

        Called by tunnel when a message with matching type arrives.
        """
        if self._shutdown:
            return
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'module.recv',
                'Module recv',
                lambda: {'type': self.TYPE, 'mid': self._module_id, 'msg': msg},
            )
        cmd = msg.get('c')
        if not cmd:
            return

        # Look for handle_<command> method
        handler = getattr(self, 'handle_' + cmd, None)
        if handler is None:
            handler = getattr(self, 'handle_unknown', None)
        if handler is None:
            if self._logger.isEnabledFor(logging.DEBUG):
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'module.command_unknown',
                    'No handler for command',
                    lambda: {'type': self.TYPE, 'mid': self._module_id, 'cmd': cmd},
                )
            return

        # Run blocking handlers in separate thread
        if getattr(handler, '_blocking', False):
            t = threading.Thread(
                target=self._run_handler,
                args=(handler, msg),
                name='%s.handle_%s' % (self.TYPE, cmd),
            )
            t.daemon = True
            with self._threads_lock:
                self._threads.append(t)
            t.start()
        else:
            self._safe_call(handler, msg)

    def _run_handler(self, handler, msg):
        """Run handler in thread with cleanup."""
        if self._shutdown:
            return
        try:
            self._safe_call(handler, msg)
        finally:
            # Clean up thread reference
            with self._threads_lock:
                current = threading.current_thread()
                if current in self._threads:
                    self._threads.remove(current)

    def _safe_call(self, handler, msg):
        """Call handler with exception logging."""
        try:
            handler(msg)
        except Exception as e:
            if self._logger.isEnabledFor(logging.ERROR):
                log_event(
                    self._logger,
                    logging.ERROR,
                    'module.handler_error',
                    'Handler error',
                    lambda: {
                        'type': self.TYPE,
                        'mid': self._module_id,
                        'error': str(e),
                    },
                    exc_info=True,
                )


class RequestResponseMixin(object):
    """
    Mixin for modules that use request-response pattern with rid correlation.

    Provides:
    - Request ID allocation
    - Pending request tracking
    - Blocking wait for responses

    Use with BaseModule:
        class MyModule(RequestResponseMixin, BaseModule):
            TYPE = 'mymod'
    """

    def __init__(self, *args, **kwargs):
        super(RequestResponseMixin, self).__init__(*args, **kwargs)
        self._rid_lock = threading.Lock()
        self._next_rid = 1
        self._pending = {}  # rid -> _PendingRequest

    def shutdown(self):
        self._clear_pending_requests()
        return super(RequestResponseMixin, self).shutdown()

    def _notify_pending_control(self, delta):
        adjust = getattr(self._tunnel, '_adjust_pending_control', None)
        if adjust is None:
            return
        adjust(delta)

    def _clear_pending_requests(self):
        with self._pending_lock:
            pending = list(self._pending.keys())
            self._pending.clear()
        if pending:
            self._notify_pending_control(-len(pending))

    def _alloc_rid(self):
        """Allocate a unique request ID."""
        with self._rid_lock:
            rid = self._next_rid
            self._next_rid += 1
            return rid

    def _register_pending(self, rid):
        """
        Register a pending request for response tracking.

        Args:
            rid: Request ID to track.

        Returns:
            _PendingRequest object to wait on.
        """
        pending = _PendingRequest()
        with self._pending_lock:
            self._pending[rid] = pending
        self._notify_pending_control(1)
        return pending

    def _wait_response(self, rid, pending, timeout=None):
        """
        Wait for a response to a pending request.

        Args:
            rid: Request ID.
            pending: _PendingRequest from _register_pending().
            timeout: Max seconds to wait.

        Returns:
            Response message dict.

        Raises:
            ModuleError: On timeout.
        """
        if not pending.event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            self._notify_pending_control(-1)
            raise ModuleError('timeout', 'request timed out')
        return pending.response or {}

    def _complete_pending(self, msg):
        """
        Complete a pending request with response.

        Call this from handle_X_ok or handle_err methods.

        Args:
            msg: Response message with 'rid' field.

        Returns:
            True if a waiter was signaled, False otherwise.
        """
        rid = msg.get('rid')
        if rid is None:
            return False
        with self._pending_lock:
            pending = self._pending.pop(rid, None)
        if pending is not None:
            pending.response = msg
            pending.event.set()
            self._notify_pending_control(-1)
            return True
        return False
