# -*- coding: ascii -*-
"""
SOCKS relay module.

Receives connection requests from the SOCKS server module and makes
outbound TCP connections to target hosts.
"""

from __future__ import absolute_import

import errno
import logging
import socket
import threading

from ..base_module import BaseModule, ModuleError, blocking
from ...logging_util import log_event
from ... import time_provider
from .relay_connection import RelayConnection
from .socks_control_messages import (
    T_SOCK,
    sock_connect_ok,
    sock_err,
)
from ..relay_logging import (
    add_fields,
    duration_secs,
    relay_fields,
)


class SocksRelayModule(BaseModule):
    """
    SOCKS5 relay module.

    Receives connection requests from the SOCKS server module and makes
    outbound TCP connections to target hosts, relaying data through
    the tunnel channel.
    """

    TYPE = T_SOCK

    @classmethod
    def run_command(cls, args, tunnel, logger):
        """Run the relay (passive - just responds to requests)."""
        module = cls(tunnel, logger=logger)
        log_event(
            logger,
            logging.INFO,
            'sock.relay_ready',
            'SOCKS relay ready',
            lambda: relay_fields(side='alice', peer='target'),
        )
        try:
            # Wait for tunnel to close
            while tunnel.connected:
                time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)
            return 0
        finally:
            module.shutdown()

    def __init__(self, tunnel, logger=None):
        super(SocksRelayModule, self).__init__(tunnel, logger=logger)
        self._config = tunnel._config

        # Active connections: ch -> _RelayConnection
        self._connections = {}
        self._connections_lock = threading.Lock()
        self._pending_connects = set()

    def shutdown(self):
        """Stop module and clean up connections."""
        # Stop all active relays
        with self._connections_lock:
            connections = list(self._connections.values())

        for conn in connections:
            conn.stop()

        super(SocksRelayModule, self).shutdown()

    @blocking
    def handle_connect(self, msg):
        """
        Handle connect request from Bob.

        Opens TCP connection to target and starts relay.
        """
        rid = msg.get('rid')
        ch = msg.get('ch')
        host = msg.get('host')
        port = msg.get('port')

        log_event(
            self._logger,
            logging.INFO,
            'sock.connect_recv',
            'SOCKS connect recv',
            lambda: add_fields(relay_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {'host': host, 'port': port}),
        )

        if not all([rid is not None, ch is not None, host, port]):
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_invalid',
                'Invalid connect request',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'reason': 'missing_fields',
                }),
            )
            return

        log_event(
            self._logger,
            logging.INFO,
            'sock.connect',
            'SOCKS connect request received',
            lambda: add_fields(relay_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {'host': host, 'port': port}),
        )

        # Get channel
        channel = self._tunnel.channel_manager.get_channel(ch)
        if channel is None:
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_channel_missing',
                'Channel not found for connect request',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {'host': host, 'port': port}),
            )
            self.send_message(sock_err(rid, ch, 'general', 'channel not found'))
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err_send',
                'SOCKS connect err send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'code': 'general',
                    'reason': 'channel not found',
                }),
            )
            return

        # Deduplicate connect requests per channel
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
                'sock.connect_duplicate',
                'Duplicate connect, reusing session',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {'state': 'reuse', 'host': host, 'port': port}),
            )
            self.send_message(sock_connect_ok(rid, ch, bind_host, bind_port))
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_ok_send',
                'SOCKS connect ok send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'bhost': bind_host,
                    'bport': bind_port,
                }),
            )
            return
        if pending:
            log_event(
                self._logger,
                logging.DEBUG,
                'sock.connect_duplicate',
                'Duplicate connect while pending',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {'state': 'pending', 'host': host, 'port': port}),
            )
            return

        # Wait for channel to open
        channel_wait_start = time_provider.now()
        if not channel.wait_open(timeout=self._config.relay_channel_open_timeout):
            channel_wait_time = duration_secs(channel_wait_start)
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_channel_failed',
                'Channel failed to open',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'wait_time': channel_wait_time,
                }),
            )
            self.send_message(sock_err(rid, ch, 'general', 'channel open failed'))
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err_send',
                'SOCKS connect err send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'code': 'general',
                    'reason': 'channel open failed',
                }),
            )
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return
        channel_wait_time = duration_secs(channel_wait_start)

        # Make TCP connection to target
        target_sock = None
        target_connect_start = time_provider.now()

        def _send_connect_error(code, reason, level=logging.INFO, exc_info=False):
            target_connect_time = duration_secs(target_connect_start)
            log_event(
                self._logger,
                logging.INFO,
                'sock.relay_target_connect',
                'SOCKS relay target connect',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'result': 'error',
                    'code': code,
                    'reason': reason,
                    'duration': target_connect_time,
                }),
            )
            log_event(
                self._logger,
                level,
                'sock.connect_err',
                'SOCKS connect error',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'code': code,
                    'reason': reason,
                    'target_connect_time': target_connect_time,
                    'channel_wait_time': channel_wait_time,
                }),
                exc_info=exc_info,
            )
            self.send_message(sock_err(rid, ch, code, reason))
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err_send',
                'SOCKS connect err send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side='alice',
                    peer='target',
                ), {
                    'host': host,
                    'port': port,
                    'code': code,
                    'reason': reason,
                }),
            )
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)

        try:
            target_sock = self._connect_target(host, port)
        except socket.gaierror as e:
            _send_connect_error('unreachable_host', str(e))
            return
        except socket.timeout:
            _send_connect_error('timeout', 'connection timeout')
            return
        except socket.error as e:
            if e.errno == errno.ECONNREFUSED:
                _send_connect_error('refused', 'connection refused')
            elif e.errno == errno.ENETUNREACH:
                _send_connect_error('unreachable_net', str(e))
            elif e.errno == errno.EHOSTUNREACH:
                _send_connect_error('unreachable_host', str(e))
            else:
                _send_connect_error('general', str(e))
            return
        except Exception as e:
            _send_connect_error('general', str(e), level=logging.ERROR, exc_info=True)
            return

        target_connect_time = duration_secs(target_connect_start)

        # Get bound address for SOCKS reply
        try:
            bound = target_sock.getsockname()
            bind_host, bind_port = bound[0], bound[1]
        except Exception:
            bind_host, bind_port = '0.0.0.0', 0

        log_event(
            self._logger,
            logging.INFO,
            'sock.relay_target_connect',
            'SOCKS relay target connect',
            lambda: add_fields(relay_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'host': host,
                'port': port,
                'result': 'ok',
                'duration': target_connect_time,
                'bhost': bind_host,
                'bport': bind_port,
            }),
        )
        log_event(
            self._logger,
            logging.INFO,
            'sock.connect_ok',
            'SOCKS connect ok',
            lambda: add_fields(relay_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'host': host,
                'port': port,
                'bhost': bind_host,
                'bport': bind_port,
                'target_connect_time': target_connect_time,
                'channel_wait_time': channel_wait_time,
            }),
        )

        # Create and register connection
        conn = RelayConnection(
            rid, ch, channel, target_sock, self._logger, self._config,
            side='alice',
            peer_label='Target',
            socket_to_channel_label='target_to_channel',
            channel_to_socket_label='channel_to_target',
            thread_names=(
                'relay-ch%d-t2c' % ch,
                'relay-ch%d-c2t' % ch,
            ),
        )
        with self._connections_lock:
            self._connections[ch] = conn
            self._pending_connects.discard(ch)

        # Send success response
        self.send_message(sock_connect_ok(rid, ch, bind_host, bind_port))
        log_event(
            self._logger,
            logging.INFO,
            'sock.connect_ok_send',
            'SOCKS connect ok send',
            lambda: add_fields(relay_fields(
                rid=rid,
                ch=ch,
                side='alice',
                peer='target',
            ), {
                'host': host,
                'port': port,
                'bhost': bind_host,
                'bport': bind_port,
            }),
        )

        # Start relay and wait for completion
        try:
            conn.start_relay()
            conn.wait()
        finally:
            self._cleanup_connection(ch)

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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout is None:
            timeout = self._config.relay_target_connect_timeout
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.settimeout(None)  # Back to blocking for relay
        return sock

    def _cleanup_connection(self, ch):
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
            'sock.relay_complete',
            'SOCKS relay complete',
            lambda: add_fields(relay_fields(
                rid=conn.rid,
                ch=conn.ch,
                side='alice',
                peer='target',
            ), summary),
        )
        log_event(
            self._logger,
            logging.DEBUG,
            'sock.cleanup',
            'Cleaned up connection',
            lambda: add_fields(relay_fields(
                rid=conn.rid,
                ch=conn.ch,
                side='alice',
                peer='target',
            ), {'connections': len(self._connections)}),
        )
