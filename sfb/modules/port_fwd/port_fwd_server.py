# -*- coding: ascii -*-
"""
Port forward server module (runs on Bob).

Accepts local TCP connections and forwards them through the tunnel
to a fixed remote host:port on Alice.
"""

from __future__ import absolute_import

import logging
import socket
import threading

from ..base_module import BaseModule, ModuleError, invalid_spec
from ..relay_connection import RelayConnection
from ..relay_control_messages import relay_connect
from ..relay_logging import (
    add_fields,
    duration_secs,
    relay_fields,
)
from ...logging_util import log_event
from ...compat import text_type
from ...utils import build_host_port_error_map, parse_host_port_or_raise
from ... import time_provider


T_FWD = 'fwd'


_HOST_PORT_ERROR_MAP = build_host_port_error_map(
    invalid_spec,
    base_message='address must be host:port',
    overrides={
        'invalid_port': 'port invalid',
        'port_range': 'port out of range',
        'ipv6_unsupported': 'address must be host:port (IPv6 unsupported)',
    },
)


class _PendingConnect(object):
    """Tracks a pending connect request awaiting response."""

    __slots__ = ('event', 'error', 'reason')

    def __init__(self):
        self.event = threading.Event()
        self.error = None
        self.reason = None


def _coerce_text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode('ascii')
        except Exception:
            return value.decode('ascii', 'replace')
    try:
        return text_type(value)
    except Exception:
        return text_type(repr(value))


class PortForwardServerModule(BaseModule):
    """
    Port forward server module.

    Accepts local TCP clients on Bob and relays connections through
    the tunnel to a fixed remote host:port on Alice.
    """

    TYPE = T_FWD
    DEFAULT_COMMAND = 'start'
    REQUIRES_COMMAND = True
    REMOTE_MODULE = 'port_fwd_relay'

    @classmethod
    def register_commands(cls, subparsers, role, config=None):
        """Register CLI subcommands for port forward server."""
        start_p = subparsers.add_parser('start', help='Start TCP port forward server')
        start_p.add_argument(
            '--local', required=True,
            help='Local listen address (HOST:PORT)'
        )
        start_p.add_argument(
            '--remote', required=True,
            help='Remote target address (HOST:PORT)'
        )

    @classmethod
    def run_command(cls, args, tunnel, logger):
        """Start the port forward server and run until tunnel closes."""
        module = cls(tunnel, logger=logger, module_id=args.module_id)
        try:
            local_spec = getattr(args, 'local', None)
            remote_spec = getattr(args, 'remote', None)
            if local_spec is None or remote_spec is None:
                raise ModuleError('invalid_spec', 'local and remote required')
            local_spec = _coerce_text(local_spec)
            remote_spec = _coerce_text(remote_spec)
            if not local_spec or not remote_spec:
                raise ModuleError('invalid_spec', 'address required')
            local_host, local_port = parse_host_port_or_raise(
                local_spec,
                _HOST_PORT_ERROR_MAP,
            )
            remote_host, remote_port = parse_host_port_or_raise(
                remote_spec,
                _HOST_PORT_ERROR_MAP,
            )
            module.start(
                listen_host=local_host,
                listen_port=local_port,
                remote_host=remote_host,
                remote_port=remote_port,
            )

            # Wait for tunnel to close
            while tunnel.connected:
                time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)
            return 0
        finally:
            module.shutdown()

    def __init__(self, tunnel, logger=None, module_id=1):
        super(PortForwardServerModule, self).__init__(
            tunnel, logger=logger, module_id=module_id
        )
        self._config = tunnel._config

        # TCP server
        self._server_socket = None
        self._accept_thread = None
        self._running = False

        # Target
        self._remote_host = None
        self._remote_port = None
        self._listen_host = None
        self._listen_port = None

        # Connection tracking
        self._connections = {}  # rid -> RelayConnection
        self._connections_lock = threading.Lock()

        # Request ID allocation
        self._rid_lock = threading.Lock()
        self._next_rid = 1

        # Pending connect requests
        self._pending = {}  # rid -> _PendingConnect
        self._pending_lock = threading.Lock()

    def _pending_count(self):
        with self._pending_lock:
            return len(self._pending)

    def _connection_count(self):
        with self._connections_lock:
            return len(self._connections)

    def start(self, listen_host, listen_port, remote_host, remote_port):
        """
        Start the port forward server.

        Args:
            listen_host: Address to listen on
            listen_port: Port to listen on
            remote_host: Remote target host
            remote_port: Remote target port
        """
        if self._running:
            raise ModuleError('already_running', 'port forward already running')

        self._listen_host = listen_host
        self._listen_port = listen_port
        self._remote_host = remote_host
        self._remote_port = remote_port

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((listen_host, listen_port))
        self._server_socket.listen(self._config.relay_listen_backlog)

        self._running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name='fwd-accept',
        )
        self._accept_thread.daemon = True
        self._accept_thread.start()

        log_event(
            self._logger,
            logging.INFO,
            'fwd.server_listen',
            'Port forward listening',
            lambda: add_fields(relay_fields(
                side='bob',
                peer='local',
            ), {
                'host': listen_host,
                'port': listen_port,
                'backlog': self._config.relay_listen_backlog,
                'remote_host': remote_host,
                'remote_port': remote_port,
            }),
        )

    def stop(self):
        """Stop the port forward server."""
        self._running = False

        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        if self._accept_thread:
            self._accept_thread.join(timeout=self._config.relay_thread_join_timeout)

        with self._connections_lock:
            connections = list(self._connections.values())

        for conn in connections:
            conn.stop()

        log_event(
            self._logger,
            logging.INFO,
            'fwd.server_stop',
            'Port forward stopped',
            lambda: add_fields(relay_fields(
                side='bob',
                peer='local',
            ), {
                'connections': self._connection_count(),
                'pending': self._pending_count(),
            }),
        )

    def shutdown(self):
        """Stop module and clean up."""
        self.stop()
        super(PortForwardServerModule, self).shutdown()

    def _alloc_rid(self):
        """Allocate a unique request ID."""
        with self._rid_lock:
            rid = self._next_rid
            self._next_rid += 1
            next_rid = self._next_rid
        log_event(
            self._logger,
            logging.DEBUG,
            'fwd.server_rid_alloc',
            'Allocated forward request id',
            lambda: add_fields(relay_fields(
                rid=rid,
                side='bob',
                peer='local',
            ), {
                'next_rid': next_rid,
                'connections': self._connection_count(),
                'pending': self._pending_count(),
            }),
        )
        return rid

    def _accept_loop(self):
        """Accept incoming connections."""
        backoff = self._config.non_blocking_poll_timeout
        max_backoff = max(self._config.relay_accept_timeout, backoff)
        self._server_socket.settimeout(self._config.relay_accept_timeout)
        while self._running:
            try:
                try:
                    client_sock, addr = self._server_socket.accept()
                except socket.timeout:
                    backoff = self._config.non_blocking_poll_timeout
                    continue

                backoff = self._config.non_blocking_poll_timeout
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'fwd.server_accept',
                    'Accepted connection',
                    lambda: add_fields(relay_fields(
                        side='bob',
                        peer='local',
                    ), {
                        'host': addr[0],
                        'port': addr[1],
                    }),
                )

                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    name='fwd-client-%s:%d' % addr,
                )
                t.daemon = True
                t.start()

            except Exception as exc:
                if self._running:
                    log_event(
                        self._logger,
                        logging.ERROR,
                        'fwd.server_accept_error',
                        'Accept error',
                        lambda: add_fields(relay_fields(
                            side='bob',
                            peer='local',
                        ), {'error': str(exc)}),
                        exc_info=True,
                    )
                    time_provider.sleep(backoff)
                    backoff = min(backoff * 2.0, max_backoff)

    def _handle_client(self, sock, addr):
        """Handle a single port forward connection."""
        rid = self._alloc_rid()
        channel = None
        conn = None
        ch_id = None
        pending = None
        cleanup_reason = 'unknown'
        connect_result = None
        connect_error = None
        channel_wait_time = None
        connect_latency = None
        connect_request_time = None
        session_start = time_provider.now()

        try:
            log_event(
                self._logger,
                logging.INFO,
                'fwd.server_connect',
                'Port forward connect requested',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    side='bob',
                    peer='local',
                ), {
                    'client_host': addr[0],
                    'client_port': addr[1],
                    'remote_host': self._remote_host,
                    'remote_port': self._remote_port,
                }),
            )

            channel = self._tunnel.channel_manager.open_channel()
            ch_id = channel.id
            channel_wait_start = time_provider.now()
            if not channel.wait_open(timeout=self._config.relay_channel_open_timeout):
                channel_wait_time = duration_secs(channel_wait_start)
                cleanup_reason = 'channel_open_failed'
                connect_result = 'channel_open_failed'
                log_event(
                    self._logger,
                    logging.WARNING,
                    'fwd.server_channel_failed',
                    'Channel open failed',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=ch_id,
                        side='bob',
                        peer='local',
                    ), {
                        'remote_host': self._remote_host,
                        'remote_port': self._remote_port,
                    }),
                )
                channel.close()
                return
            channel_wait_time = duration_secs(channel_wait_start)

            conn = RelayConnection(
                rid, channel.id, channel, sock, self._logger, self._config,
                side='bob',
                peer_label='Local',
                socket_to_channel_label='local_to_channel',
                channel_to_socket_label='channel_to_local',
                thread_names=(
                    'fwd-rid%d-l2ch' % rid,
                    'fwd-rid%d-ch2l' % rid,
                ),
                event_prefix='fwd',
            )
            with self._connections_lock:
                self._connections[rid] = conn

            pending = _PendingConnect()
            with self._pending_lock:
                self._pending[rid] = pending
                pending_count = len(self._pending)
            log_event(
                self._logger,
                logging.DEBUG,
                'fwd.server_pending_add',
                'Forward connect pending',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='local',
                ), {'pending': pending_count}),
            )

            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_send',
                'Forward connect send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='local',
                ), {
                    'remote_host': self._remote_host,
                    'remote_port': self._remote_port,
                }),
            )
            connect_request_time = time_provider.now()
            self.send_message(
                relay_connect(T_FWD, rid, channel.id, self._remote_host, self._remote_port)
            )

            if not pending.event.wait(timeout=self._config.relay_connect_timeout):
                connect_latency = duration_secs(connect_request_time)
                cleanup_reason = 'connect_timeout'
                connect_result = 'timeout'
                log_event(
                    self._logger,
                    logging.WARNING,
                    'fwd.server_connect_timeout',
                    'Connect timeout',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='local',
                    ), {
                        'remote_host': self._remote_host,
                        'remote_port': self._remote_port,
                    }),
                )
                return

            connect_latency = duration_secs(connect_request_time)
            if pending.error:
                cleanup_reason = 'connect_failed'
                connect_result = 'error'
                connect_error = pending.error
                log_event(
                    self._logger,
                    logging.INFO,
                    'fwd.server_connect_failed',
                    'Connect failed',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='local',
                    ), {
                        'remote_host': self._remote_host,
                        'remote_port': self._remote_port,
                        'error': pending.error,
                        'reason': pending.reason,
                    }),
                )
                return

            connect_result = 'ok'
            log_event(
                self._logger,
                logging.INFO,
                'fwd.server_connected',
                'Connected',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='local',
                ), {
                    'remote_host': self._remote_host,
                    'remote_port': self._remote_port,
                }),
            )

            conn.start_relay()
            conn.wait()
            cleanup_reason = 'relay_complete'

        except Exception as exc:
            cleanup_reason = 'client_handler_error'
            connect_result = 'handler_error'
            connect_error = str(exc)
            log_event(
                self._logger,
                logging.ERROR,
                'fwd.server_client_error',
                'Client handler error',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='local',
                ), {'error': str(exc)}),
                exc_info=True,
            )
        finally:
            log_event(
                self._logger,
                logging.INFO,
                'fwd.server_session',
                'Port forward session',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='local',
                ), {
                    'client_host': addr[0],
                    'client_port': addr[1],
                    'remote_host': self._remote_host,
                    'remote_port': self._remote_port,
                    'channel_wait_time': channel_wait_time,
                    'connect_latency': connect_latency,
                    'session_time': duration_secs(session_start),
                    'connect_result': connect_result,
                    'connect_error': connect_error,
                }),
            )
            self._cleanup_connection(rid, reason=cleanup_reason)
            if conn is None:
                if channel is not None:
                    try:
                        channel.close()
                    except Exception:
                        pass
                try:
                    sock.close()
                except Exception:
                    pass

    def _cleanup_connection(self, rid, reason=None):
        """Clean up connection resources."""
        pending_removed = False
        with self._pending_lock:
            if rid in self._pending:
                pending_removed = True
            self._pending.pop(rid, None)

        with self._connections_lock:
            conn = self._connections.pop(rid, None)

        if conn:
            conn.stop()
            log_event(
                self._logger,
                logging.DEBUG,
                'fwd.server_cleanup',
                'Cleaned up connection',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=conn.ch,
                    side='bob',
                    peer='local',
                ), {
                    'reason': reason,
                    'pending_removed': pending_removed,
                    'connections': self._connection_count(),
                    'pending': self._pending_count(),
                }),
            )

    # --- Response Handlers ---

    def handle_connect_ok(self, msg):
        """Handle connect_ok from Alice."""
        rid = msg.get('rid')
        if rid is None:
            return

        with self._pending_lock:
            pending = self._pending.get(rid)

        if pending:
            pending.event.set()
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_ok_recv',
                'Forward connect ok recv',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=msg.get('ch'),
                    side='bob',
                    peer='local',
                ), {
                    'bhost': msg.get('bhost'),
                    'bport': msg.get('bport'),
                }),
            )

    def handle_err(self, msg):
        """Handle error from Alice."""
        rid = msg.get('rid')
        if rid is None:
            return

        with self._pending_lock:
            pending = self._pending.get(rid)

        if pending:
            pending.error = msg.get('code', 'general')
            pending.reason = msg.get('reason')
            pending.event.set()
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_err_recv',
                'Forward connect err recv',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=msg.get('ch'),
                    side='bob',
                    peer='local',
                ), {
                    'code': msg.get('code'),
                    'reason': msg.get('reason'),
                }),
            )
