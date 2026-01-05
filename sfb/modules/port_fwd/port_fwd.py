# -*- coding: ascii -*-
"""
Port forward module.
"""

from __future__ import absolute_import

import errno
import logging
import socket
import threading

from ..base_module import BaseModule, ModuleError, RequestResponseMixin, blocking
from ...compat import PY2, text_type, to_native_str
from ...logging_util import log_event
from ... import time_provider
from .relay_connection import RelayConnection
from .port_fwd_control_messages import (
    T_FWD,
    fwd_connect,
    fwd_connect_ok,
    fwd_err,
)
from .port_fwd_logging import (
    add_fields,
    duration_secs,
    fwd_fields,
)


class PortForwardError(ModuleError):
    """Port forward module error."""


class PortForwardModule(RequestResponseMixin, BaseModule):
    """
    TCP port forward module.

    Bob listens locally and forwards each incoming connection to a fixed
    target on Alice.
    """

    TYPE = T_FWD
    DEFAULT_COMMAND = None
    REQUIRES_COMMAND = False
    REMOTE_MODULE = 'port_fwd'
    USES_SUBCOMMANDS = False

    @classmethod
    def register_commands(cls, subparsers, role, config=None):
        if role != 'server':
            return
        backlog_default = 5
        timeout_default = None
        if config is not None:
            backlog_default = config.port_fwd_listen_backlog
            timeout_default = config.port_fwd_connect_timeout
        group = subparsers.add_argument_group(
            'port_fwd options',
            'Forward TCP connections from Bob to a fixed Alice-side target.
'
            'Address format: host:port or [::1]:port.',
        )
        group.add_argument(
            '--local',
            required=True,
            metavar='HOST:PORT',
            help='Bob-side listen address',
        )
        group.add_argument(
            '--remote',
            required=True,
            metavar='HOST:PORT',
            help='Alice-side target address',
        )
        group.add_argument(
            '--backlog',
            type=int,
            default=backlog_default,
            help='Listen backlog (default: %s)' % backlog_default,
        )
        group.add_argument(
            '--timeout',
            type=float,
            default=timeout_default,
            help='Connect timeout in seconds (default: %s)' % timeout_default,
        )

    @classmethod
    def run_command(cls, args, tunnel, logger):
        module = cls(tunnel, logger=logger)
        local_spec = getattr(args, 'local', None)
        remote_spec = getattr(args, 'remote', None)
        backlog = getattr(args, 'backlog', None)
        timeout = getattr(args, 'timeout', None)
        try:
            module.start(
                local_spec=local_spec,
                remote_spec=remote_spec,
                backlog=backlog,
                connect_timeout=timeout,
            )
            while tunnel.connected:
                time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)
            return 0
        finally:
            module.shutdown()

    def __init__(self, tunnel, logger=None):
        super(PortForwardModule, self).__init__(tunnel, logger=logger)
        self._config = tunnel._config

        self._server_socket = None
        self._accept_thread = None
        self._running = False
        self._listen_host = None
        self._listen_port = None
        self._remote_host = None
        self._remote_port = None
        self._connect_timeout = None

        self._connections = {}
        self._connections_lock = threading.Lock()
        self._pending_connects = set()

    def shutdown(self):
        """Stop module and clean up connections."""
        self.stop()
        with self._connections_lock:
            connections = list(self._connections.values())
        for conn in connections:
            conn.stop()
        super(PortForwardModule, self).shutdown()

    def start(self, local_spec, remote_spec, backlog=None, connect_timeout=None):
        """
        Start the port forward listener on Bob.

        Args:
            local_spec: Bob-side listen address (host:port)
            remote_spec: Alice-side target address (host:port)
            backlog: Optional listen backlog
            connect_timeout: Optional connect timeout override
        """
        if self._running:
            raise PortForwardError('already_running', 'port forward already running')

        local_host, local_port = _parse_host_port(local_spec, 'local')
        remote_host, remote_port = _parse_host_port(remote_spec, 'remote')

        if backlog is None:
            backlog = self._config.port_fwd_listen_backlog
        if backlog is not None and backlog < 1:
            raise PortForwardError('invalid_backlog', 'backlog must be >= 1')

        self._listen_host = local_host
        self._listen_port = local_port
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._connect_timeout = connect_timeout

        self._server_socket = _create_server_socket(local_host, local_port, backlog)
        self._server_socket.settimeout(self._config.port_fwd_accept_timeout)
        self._running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name='port-fwd-accept',
        )
        self._accept_thread.daemon = True
        self._accept_thread.start()

        log_event(
            self._logger,
            logging.INFO,
            'fwd.server_listen',
            'Port forward listening',
            lambda: add_fields(fwd_fields(
                side='bob',
                peer='client',
            ), {
                'local_host': local_host,
                'local_port': local_port,
                'remote_host': remote_host,
                'remote_port': remote_port,
                'backlog': backlog,
            }),
        )

    def stop(self):
        """Stop the port forward listener."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        if self._accept_thread:
            self._accept_thread.join(timeout=self._config.port_fwd_thread_join_timeout)

        log_event(
            self._logger,
            logging.INFO,
            'fwd.server_stop',
            'Port forward stopped',
            lambda: add_fields(fwd_fields(
                side='bob',
                peer='client',
            ), {
                'connections': self._connection_count(),
                'pending': self._pending_count(),
            }),
        )

    def _pending_count(self):
        with self._pending_lock:
            return len(getattr(self, '_pending', {}))

    def _connection_count(self):
        with self._connections_lock:
            return len(self._connections)

    def _accept_loop(self):
        """Accept incoming connections."""
        backoff = self._config.non_blocking_poll_timeout
        max_backoff = max(self._config.port_fwd_accept_timeout, backoff)
        while self._running:
            try:
                try:
                    client_sock, addr = self._server_socket.accept()
                except socket.timeout:
                    backoff = self._config.non_blocking_poll_timeout
                    continue

                backoff = self._config.non_blocking_poll_timeout
                client_host, client_port = _split_addr(addr)
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'fwd.server_accept',
                    'Accepted connection',
                    lambda: add_fields(fwd_fields(
                        side='bob',
                        peer='client',
                    ), {
                        'client_host': client_host,
                        'client_port': client_port,
                    }),
                )

                thread_name = _format_thread_name('port-fwd-client', addr)
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    name=thread_name,
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
                        lambda: add_fields(fwd_fields(
                            side='bob',
                            peer='client',
                        ), {'error': str(exc)}),
                        exc_info=True,
                    )
                    time_provider.sleep(backoff)
                    backoff = min(backoff * 2.0, max_backoff)

    def _handle_client(self, sock, addr):
        rid = self._alloc_rid()
        channel = None
        conn = None
        ch_id = None
        cleanup_reason = 'unknown'
        connect_result = None
        connect_error = None
        connect_latency = None
        channel_wait_time = None
        connect_request_time = None
        handshake_start = time_provider.now()
        client_host, client_port = _split_addr(addr)
        remote_host = self._remote_host
        remote_port = self._remote_port

        try:
            channel = self._tunnel.channel_manager.open_channel()
            ch_id = channel.id
            channel_wait_start = time_provider.now()
            if not channel.wait_open(timeout=self._config.port_fwd_channel_open_timeout):
                channel_wait_time = duration_secs(channel_wait_start)
                cleanup_reason = 'channel_open_failed'
                connect_result = 'channel_open_failed'
                log_event(
                    self._logger,
                    logging.WARNING,
                    'fwd.server_channel_failed',
                    'Channel open failed',
                    lambda: add_fields(fwd_fields(
                        rid=rid,
                        ch=ch_id,
                        side='bob',
                        peer='client',
                    ), {
                        'remote_host': remote_host,
                        'remote_port': remote_port,
                        'client_host': client_host,
                        'client_port': client_port,
                        'wait_time': channel_wait_time,
                    }),
                )
                _close_socket(sock)
                channel.close()
                return
            channel_wait_time = duration_secs(channel_wait_start)

            conn = RelayConnection(
                rid,
                channel.id,
                channel,
                sock,
                self._logger,
                self._config,
                side='bob',
                peer_label='Client',
                socket_to_channel_label='client_to_channel',
                channel_to_socket_label='channel_to_client',
                thread_names=(
                    'fwd-rid%d-c2ch' % rid,
                    'fwd-rid%d-ch2c' % rid,
                ),
            )
            with self._connections_lock:
                self._connections[channel.id] = conn

            pending = self._register_pending(rid)
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_send',
                'Port forward connect send',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='client',
                ), {
                    'remote_host': remote_host,
                    'remote_port': remote_port,
                    'client_host': client_host,
                    'client_port': client_port,
                }),
            )
            connect_request_time = time_provider.now()
            self.send_message(fwd_connect(rid, channel.id, remote_host, remote_port))

            timeout = self._connect_timeout
            if timeout is None:
                timeout = self._config.port_fwd_connect_timeout
            try:
                resp = self._wait_response(rid, pending, timeout=timeout)
            except ModuleError:
                connect_latency = duration_secs(connect_request_time)
                cleanup_reason = 'connect_timeout'
                connect_result = 'timeout'
                log_event(
                    self._logger,
                    logging.WARNING,
                    'fwd.server_connect_timeout',
                    'Connect timeout',
                    lambda: add_fields(fwd_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='client',
                    ), {
                        'remote_host': remote_host,
                        'remote_port': remote_port,
                        'timeout': timeout,
                    }),
                )
                return

            connect_latency = duration_secs(connect_request_time)
            cmd = resp.get('c')
            if cmd != 'connect_ok':
                cleanup_reason = 'connect_failed'
                connect_result = 'error'
                connect_error = resp.get('code')
                log_event(
                    self._logger,
                    logging.INFO,
                    'fwd.server_connect_failed',
                    'Connect failed',
                    lambda: add_fields(fwd_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='client',
                    ), {
                        'remote_host': remote_host,
                        'remote_port': remote_port,
                        'code': resp.get('code'),
                        'reason': resp.get('reason'),
                    }),
                )
                return

            connect_result = 'ok'
            log_event(
                self._logger,
                logging.INFO,
                'fwd.server_connected',
                'Port forward connected',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='client',
                ), {
                    'remote_host': remote_host,
                    'remote_port': remote_port,
                    'bhost': resp.get('bhost'),
                    'bport': resp.get('bport'),
                }),
            )

            conn.start_relay()
            conn.wait()
            cleanup_reason = 'relay_complete'

        except Exception as exc:
            cleanup_reason = 'client_handler_error'
            connect_result = connect_result or 'handler_error'
            connect_error = str(exc)
            log_event(
                self._logger,
                logging.ERROR,
                'fwd.server_client_error',
                'Client handler error',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='client',
                ), {
                    'error': str(exc),
                }),
                exc_info=True,
            )
        finally:
            log_event(
                self._logger,
                logging.INFO,
                'fwd.server_handshake',
                'Port forward handshake',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='client',
                ), {
                    'client_host': client_host,
                    'client_port': client_port,
                    'remote_host': remote_host,
                    'remote_port': remote_port,
                    'channel_wait_time': channel_wait_time,
                    'connect_latency': connect_latency,
                    'handshake_time': duration_secs(handshake_start),
                    'connect_result': connect_result,
                    'connect_error': connect_error,
                }),
            )
            if ch_id is not None:
                self._cleanup_connection(
                    ch_id,
                    side='bob',
                    peer='client',
                    reason=cleanup_reason,
                )
            else:
                _close_socket(sock)
                if channel is not None:
                    channel.close()

    def _cleanup_connection(self, ch, side, peer, reason=None):
        """Clean up connection resources."""
        with self._connections_lock:
            conn = self._connections.pop(ch, None)
        if conn is None:
            return
        conn.stop()
        summary = conn.get_summary()
        log_event(
            self._logger,
            logging.INFO,
            'fwd.relay_complete',
            'Port forward relay complete',
            lambda: add_fields(fwd_fields(
                rid=conn.rid,
                ch=conn.ch,
                side=side,
                peer=peer,
            ), summary),
        )
        log_event(
            self._logger,
            logging.DEBUG,
            'fwd.cleanup',
            'Cleaned up connection',
            lambda: add_fields(fwd_fields(
                rid=conn.rid,
                ch=conn.ch,
                side=side,
                peer=peer,
            ), {
                'reason': reason,
                'connections': self._connection_count(),
                'pending': self._pending_count(),
            }),
        )

    def handle_connect_ok(self, msg):
        """Handle connect_ok from Alice."""
        if not self._complete_pending(msg):
            return
        log_event(
            self._logger,
            logging.INFO,
            'fwd.connect_ok_recv',
            'Port forward connect ok recv',
            lambda: add_fields(fwd_fields(
                rid=msg.get('rid'),
                ch=msg.get('ch'),
                side='bob',
                peer='client',
            ), {
                'bhost': msg.get('bhost'),
                'bport': msg.get('bport'),
            }),
        )

    def handle_err(self, msg):
        """Handle error from Alice."""
        if not self._complete_pending(msg):
            return
        log_event(
            self._logger,
            logging.INFO,
            'fwd.connect_err_recv',
            'Port forward connect err recv',
            lambda: add_fields(fwd_fields(
                rid=msg.get('rid'),
                ch=msg.get('ch'),
                side='bob',
                peer='client',
            ), {
                'code': msg.get('code'),
                'reason': msg.get('reason'),
            }),
        )

    @blocking
    def handle_connect(self, msg):
        """
        Handle connect request from Bob (Alice side).
        """
        rid = msg.get('rid')
        ch = msg.get('ch')
        host = msg.get('host')
        port = msg.get('port')

        log_event(
            self._logger,
            logging.INFO,
            'fwd.connect_recv',
            'Port forward connect recv',
            lambda: add_fields(fwd_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'remote_host': host,
                'remote_port': port,
            }),
        )

        if not all([rid is not None, ch is not None, host, port]):
            log_event(
                self._logger,
                logging.WARNING,
                'fwd.connect_invalid',
                'Invalid connect request',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'reason': 'missing_fields',
                }),
            )
            return

        channel = self._tunnel.channel_manager.get_channel(ch)
        if channel is None:
            log_event(
                self._logger,
                logging.WARNING,
                'fwd.connect_channel_missing',
                'Channel not found for connect request',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                }),
            )
            self.send_message(fwd_err(rid, ch, 'general', 'channel not found'))
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_err_send',
                'Port forward connect err send',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'code': 'general',
                    'reason': 'channel not found',
                }),
            )
            return

        reuse_sock = None
        pending = False
        with self._connections_lock:
            existing = self._connections.get(ch)
            if existing is not None:
                reuse_sock = existing.sock
            elif ch in self._pending_connects:
                pending = True
            else:
                self._pending_connects.add(ch)

        if reuse_sock is not None:
            try:
                bind_host, bind_port = reuse_sock.getsockname()
            except Exception:
                bind_host, bind_port = '0.0.0.0', 0
            log_event(
                self._logger,
                logging.DEBUG,
                'fwd.connect_duplicate',
                'Duplicate connect, reusing session',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'state': 'reuse',
                    'remote_host': host,
                    'remote_port': port,
                }),
            )
            self.send_message(fwd_connect_ok(rid, ch, bind_host, bind_port))
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_ok_send',
                'Port forward connect ok send',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'bhost': bind_host,
                    'bport': bind_port,
                }),
            )
            return
        if pending:
            log_event(
                self._logger,
                logging.DEBUG,
                'fwd.connect_duplicate',
                'Duplicate connect while pending',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'state': 'pending',
                    'remote_host': host,
                    'remote_port': port,
                }),
            )
            return

        channel_wait_start = time_provider.now()
        if not channel.wait_open(timeout=self._config.port_fwd_channel_open_timeout):
            channel_wait_time = duration_secs(channel_wait_start)
            log_event(
                self._logger,
                logging.WARNING,
                'fwd.connect_channel_failed',
                'Channel failed to open',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'wait_time': channel_wait_time,
                }),
            )
            self.send_message(fwd_err(rid, ch, 'general', 'channel open failed'))
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_err_send',
                'Port forward connect err send',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'code': 'general',
                    'reason': 'channel open failed',
                }),
            )
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return
        channel_wait_time = duration_secs(channel_wait_start)

        target_sock = None
        target_connect_start = time_provider.now()

        def _send_connect_error(code, reason, level=logging.INFO, exc_info=False):
            target_connect_time = duration_secs(target_connect_start)
            log_event(
                self._logger,
                logging.INFO,
                'fwd.relay_target_connect',
                'Port forward target connect',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'result': 'error',
                    'code': code,
                    'reason': reason,
                    'duration': target_connect_time,
                }),
            )
            log_event(
                self._logger,
                level,
                'fwd.connect_err',
                'Port forward connect error',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'code': code,
                    'reason': reason,
                    'target_connect_time': target_connect_time,
                    'channel_wait_time': channel_wait_time,
                }),
                exc_info=exc_info,
            )
            self.send_message(fwd_err(rid, ch, code, reason))
            log_event(
                self._logger,
                logging.INFO,
                'fwd.connect_err_send',
                'Port forward connect err send',
                lambda: add_fields(fwd_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'remote_host': host,
                    'remote_port': port,
                    'code': code,
                    'reason': reason,
                }),
            )
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)

        try:
            target_sock = self._connect_target(host, port)
        except socket.gaierror as exc:
            _send_connect_error('unreachable_host', str(exc))
            return
        except socket.timeout:
            _send_connect_error('timeout', 'connection timeout')
            return
        except socket.error as exc:
            if exc.errno == errno.ECONNREFUSED:
                _send_connect_error('refused', 'connection refused')
            elif exc.errno == errno.ENETUNREACH:
                _send_connect_error('unreachable_net', str(exc))
            elif exc.errno == errno.EHOSTUNREACH:
                _send_connect_error('unreachable_host', str(exc))
            else:
                _send_connect_error('general', str(exc))
            return
        except Exception as exc:
            _send_connect_error('general', str(exc), level=logging.ERROR, exc_info=True)
            return

        target_connect_time = duration_secs(target_connect_start)
        try:
            bind = target_sock.getsockname()
            bind_host, bind_port = bind[0], bind[1]
        except Exception:
            bind_host, bind_port = '0.0.0.0', 0

        log_event(
            self._logger,
            logging.INFO,
            'fwd.relay_target_connect',
            'Port forward target connect',
            lambda: add_fields(fwd_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'remote_host': host,
                'remote_port': port,
                'result': 'ok',
                'duration': target_connect_time,
                'bhost': bind_host,
                'bport': bind_port,
            }),
        )
        log_event(
            self._logger,
            logging.INFO,
            'fwd.connect_ok',
            'Port forward connect ok',
            lambda: add_fields(fwd_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'remote_host': host,
                'remote_port': port,
                'bhost': bind_host,
                'bport': bind_port,
                'target_connect_time': target_connect_time,
                'channel_wait_time': channel_wait_time,
            }),
        )

        conn = RelayConnection(
            rid,
            ch,
            channel,
            target_sock,
            self._logger,
            self._config,
            side='alice',
            peer_label='Target',
            socket_to_channel_label='target_to_channel',
            channel_to_socket_label='channel_to_target',
            thread_names=(
                'fwd-ch%d-t2c' % ch,
                'fwd-ch%d-c2t' % ch,
            ),
        )
        with self._connections_lock:
            self._connections[ch] = conn
            self._pending_connects.discard(ch)

        self.send_message(fwd_connect_ok(rid, ch, bind_host, bind_port))
        log_event(
            self._logger,
            logging.INFO,
            'fwd.connect_ok_send',
            'Port forward connect ok send',
            lambda: add_fields(fwd_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'remote_host': host,
                'remote_port': port,
                'bhost': bind_host,
                'bport': bind_port,
            }),
        )

        try:
            conn.start_relay()
            conn.wait()
        finally:
            self._cleanup_connection(ch, side='alice', peer='target')

    def _connect_target(self, host, port, timeout=None):
        """
        Connect to target host with timeout.

        Args:
            host: Target hostname or IP
            port: Target port
            timeout: Connection timeout in seconds

        Returns:
            Connected socket

        Raises:
            socket.error: On connection failure
        """
        if timeout is None:
            timeout = self._config.port_fwd_connect_target_timeout
        addrinfo = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        last_exc = None
        for family, socktype, proto, _, addr in addrinfo:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(timeout)
                sock.connect(addr)
                sock.settimeout(None)
                return sock
            except socket.error as exc:
                last_exc = exc
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            except Exception as exc:
                last_exc = exc
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
        if last_exc:
            raise last_exc
        raise socket.error('connect failed')


def _format_thread_name(prefix, addr):
    host, port = _split_addr(addr)
    if host is None:
        host = 'unknown'
    if port is None:
        port = 0
    return '%s-%s:%s' % (prefix, host, port)


def _split_addr(addr):
    if isinstance(addr, tuple) and len(addr) >= 2:
        return addr[0], addr[1]
    return None, None


def _close_socket(sock):
    if sock is None:
        return
    try:
        sock.close()
    except Exception:
        pass


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        if PY2:
            return value
        try:
            return value.decode('utf-8')
        except Exception:
            return value.decode('utf-8', 'replace')
    return to_native_str(value)


def _parse_host_port(value, label):
    value = _coerce_text(value)
    if not value:
        raise PortForwardError('invalid_addr', '%s address required' % label)
    if value.startswith('['):
        end = value.find(']')
        if end == -1:
            raise PortForwardError('invalid_addr', '%s address invalid' % label)
        if len(value) <= end + 2 or value[end + 1] != ':':
            raise PortForwardError('invalid_addr', '%s address invalid' % label)
        host = value[1:end]
        port_text = value[end + 2:]
    else:
        if value.count(':') != 1:
            raise PortForwardError('invalid_addr', '%s address invalid' % label)
        host, port_text = value.rsplit(':', 1)
    if not host or not port_text:
        raise PortForwardError('invalid_addr', '%s address invalid' % label)
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        raise PortForwardError('invalid_addr', '%s port invalid' % label)
    if port < 1 or port > 65535:
        raise PortForwardError('invalid_addr', '%s port out of range' % label)
    return host, port


def _create_server_socket(host, port, backlog):
    addrinfo = socket.getaddrinfo(
        host,
        port,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
    )
    last_exc = None
    for family, socktype, proto, _, addr in addrinfo:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IPV6,
                        socket.IPV6_V6ONLY,
                        0,
                    )
                except Exception:
                    pass
            sock.bind(addr)
            sock.listen(backlog)
            return sock
        except Exception as exc:
            last_exc = exc
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
    if last_exc:
        raise PortForwardError('listen_failed', str(last_exc))
    raise PortForwardError('listen_failed', 'listen failed')
