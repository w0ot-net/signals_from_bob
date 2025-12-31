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
from .data_pump import pump_channel_to_socket, pump_socket_to_channel
from .socks_control_messages import T_SOCK, sock_connect_ok, sock_err


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
        import time
        module = cls(tunnel, logger=logger)
        log_event(
            logger,
            logging.INFO,
            'sock.relay_ready',
            'SOCKS relay ready',
        )
        try:
            # Wait for tunnel to close
            while tunnel.is_connected:
                time.sleep(tunnel._config.tunnel_connect_poll_interval)
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

        if not all([rid is not None, ch is not None, host, port]):
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_invalid',
                'Invalid connect request',
                {'msg': msg},
            )
            return

        log_event(
            self._logger,
            logging.INFO,
            'sock.connect',
            'SOCKS connect request received',
            {'rid': rid, 'ch': ch, 'host': host, 'port': port, 'side': 'alice'},
        )

        # Get channel
        channel = self._tunnel.channel_manager.get_channel(ch)
        if channel is None:
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_channel_missing',
                'Channel not found for connect request',
                {'ch': ch},
            )
            self.send_message(sock_err(rid, ch, 'general', 'channel not found'))
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
                {'ch': ch},
            )
            self.send_message(sock_connect_ok(rid, ch, bind_host, bind_port))
            return
        if pending:
            log_event(
                self._logger,
                logging.DEBUG,
                'sock.connect_duplicate',
                'Duplicate connect while pending',
                {'ch': ch},
            )
            return

        # Wait for channel to open
        if not channel.wait_open(timeout=self._config.socks_channel_open_timeout):
            log_event(
                self._logger,
                logging.WARNING,
                'sock.connect_channel_failed',
                'Channel failed to open',
                {'ch': ch},
            )
            self.send_message(sock_err(rid, ch, 'general', 'channel open failed'))
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return

        # Make TCP connection to target
        target_sock = None
        try:
            target_sock = self._connect_target(host, port)
        except socket.gaierror as e:
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err',
                'SOCKS connect error',
                {'rid': rid, 'ch': ch, 'code': 'unreachable_host', 'reason': str(e), 'side': 'alice'},
            )
            self.send_message(sock_err(rid, ch, 'unreachable_host', str(e)))
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return
        except socket.timeout:
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err',
                'SOCKS connect error',
                {'rid': rid, 'ch': ch, 'code': 'timeout', 'reason': 'connection timeout', 'side': 'alice'},
            )
            self.send_message(sock_err(rid, ch, 'timeout', 'connection timeout'))
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return
        except socket.error as e:
            if e.errno == errno.ECONNREFUSED:
                log_event(
                    self._logger,
                    logging.INFO,
                    'sock.connect_err',
                    'SOCKS connect error',
                    {'rid': rid, 'ch': ch, 'code': 'refused', 'reason': 'connection refused', 'side': 'alice'},
                )
                self.send_message(sock_err(rid, ch, 'refused', 'connection refused'))
            elif e.errno == errno.ENETUNREACH:
                log_event(
                    self._logger,
                    logging.INFO,
                    'sock.connect_err',
                    'SOCKS connect error',
                    {'rid': rid, 'ch': ch, 'code': 'unreachable_net', 'reason': str(e), 'side': 'alice'},
                )
                self.send_message(sock_err(rid, ch, 'unreachable_net', str(e)))
            elif e.errno == errno.EHOSTUNREACH:
                log_event(
                    self._logger,
                    logging.INFO,
                    'sock.connect_err',
                    'SOCKS connect error',
                    {'rid': rid, 'ch': ch, 'code': 'unreachable_host', 'reason': str(e), 'side': 'alice'},
                )
                self.send_message(sock_err(rid, ch, 'unreachable_host', str(e)))
            else:
                log_event(
                    self._logger,
                    logging.INFO,
                    'sock.connect_err',
                    'SOCKS connect error',
                    {'rid': rid, 'ch': ch, 'code': 'general', 'reason': str(e), 'side': 'alice'},
                )
                self.send_message(sock_err(rid, ch, 'general', str(e)))
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return
        except Exception as e:
            log_event(
                self._logger,
                logging.ERROR,
                'sock.connect_err',
                'Unexpected SOCKS connect error',
                {
                    'rid': rid,
                    'ch': ch,
                    'code': 'general',
                    'reason': str(e),
                    'side': 'alice',
                    'host': host,
                    'port': port,
                },
                exc_info=True,
            )
            self.send_message(sock_err(rid, ch, 'general', str(e)))
            channel.close()
            with self._connections_lock:
                self._pending_connects.discard(ch)
            return

        # Get bound address for SOCKS reply
        try:
            bound = target_sock.getsockname()
            bind_host, bind_port = bound[0], bound[1]
        except Exception:
            bind_host, bind_port = '0.0.0.0', 0

        log_event(
            self._logger,
            logging.INFO,
            'sock.connect_ok',
            'SOCKS connect ok',
            {'rid': rid, 'ch': ch, 'bhost': bind_host, 'bport': bind_port, 'side': 'alice'},
        )

        # Create and register connection
        conn = _RelayConnection(rid, ch, channel, target_sock,
                                self._logger, self._config)
        with self._connections_lock:
            self._connections[ch] = conn
            self._pending_connects.discard(ch)

        # Send success response
        self.send_message(sock_connect_ok(rid, ch, bind_host, bind_port))

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
            timeout = self._config.socks_connect_target_timeout
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
        log_event(
            self._logger,
            logging.DEBUG,
            'sock.cleanup',
            'Cleaned up connection',
            {'ch': ch},
        )


class _RelayConnection(object):
    """Manages a single relay connection between channel and target socket."""

    __slots__ = (
        'rid', 'ch', 'channel', 'sock', '_logger', '_config',
        '_stop_event', '_threads', '_error',
    )

    def __init__(self, rid, ch, channel, sock, logger, config):
        self.rid = rid
        self.ch = ch
        self.channel = channel
        self.sock = sock
        self._logger = logger
        self._config = config
        self._stop_event = threading.Event()
        self._threads = []
        self._error = None

    def start_relay(self):
        """Start bidirectional relay threads."""
        # Channel -> Target
        t1 = threading.Thread(
            target=self._relay_channel_to_target,
            name='relay-ch%d-c2t' % self.ch,
        )
        t1.daemon = True

        # Target -> Channel
        t2 = threading.Thread(
            target=self._relay_target_to_channel,
            name='relay-ch%d-t2c' % self.ch,
        )
        t2.daemon = True

        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def _relay_channel_to_target(self):
        """Relay data from channel to target socket."""
        pump_channel_to_socket(
            self.channel,
            self.sock,
            self._config,
            self._logger,
            self._stop_event,
            self.rid,
            self.ch,
            'alice',
            'Target',
            'channel_to_target',
        )

    def _relay_target_to_channel(self):
        """Relay data from target socket to channel."""
        pump_socket_to_channel(
            self.sock,
            self.channel,
            self._config,
            self._logger,
            self._stop_event,
            self.rid,
            self.ch,
            'alice',
            'Target',
            'target_to_channel',
        )

    def wait(self, timeout=None):
        """Wait for relay threads to complete."""
        for t in self._threads:
            t.join(timeout=timeout)

    def stop(self):
        """Signal relay to stop and close resources."""
        self._stop_event.set()

        # Close socket to unblock recv
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        # Close channel (notifies peer automatically)
        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass

        # Wait for threads with timeout
        for t in self._threads:
            t.join(timeout=self._config.socks_thread_join_timeout)
