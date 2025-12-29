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
        logger.info('SOCKS relay ready')
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
            self._logger.warning('Invalid connect request: %s', msg)
            return

        self._logger.info('Connect request: %s:%d (rid=%d ch=%d)',
                          host, port, rid, ch)

        # Get channel
        channel = self._tunnel.channel_manager.get_channel(ch)
        if channel is None:
            self._logger.warning('Channel %d not found for connect request', ch)
            self.send_message(sock_err(rid, ch, 'general', 'channel not found'))
            return

        # Wait for channel to open
        if not channel.wait_open(timeout=self._config.socks_channel_open_timeout):
            self._logger.warning('Channel %d failed to open', ch)
            self.send_message(sock_err(rid, ch, 'general', 'channel open failed'))
            return

        # Make TCP connection to target
        target_sock = None
        try:
            target_sock = self._connect_target(host, port)
        except socket.gaierror as e:
            self._logger.info('DNS resolution failed for %s: %s', host, e)
            self.send_message(sock_err(rid, ch, 'unreachable_host', str(e)))
            channel.close()
            return
        except socket.timeout:
            self._logger.info('Connection to %s:%d timed out', host, port)
            self.send_message(sock_err(rid, ch, 'timeout', 'connection timeout'))
            channel.close()
            return
        except socket.error as e:
            if e.errno == errno.ECONNREFUSED:
                self._logger.info('Connection to %s:%d refused', host, port)
                self.send_message(sock_err(rid, ch, 'refused', 'connection refused'))
            elif e.errno == errno.ENETUNREACH:
                self._logger.info('Network unreachable for %s:%d', host, port)
                self.send_message(sock_err(rid, ch, 'unreachable_net', str(e)))
            elif e.errno == errno.EHOSTUNREACH:
                self._logger.info('Host unreachable: %s:%d', host, port)
                self.send_message(sock_err(rid, ch, 'unreachable_host', str(e)))
            else:
                self._logger.info('Connection to %s:%d failed: %s', host, port, e)
                self.send_message(sock_err(rid, ch, 'general', str(e)))
            channel.close()
            return
        except Exception as e:
            self._logger.exception('Unexpected error connecting to %s:%d', host, port)
            self.send_message(sock_err(rid, ch, 'general', str(e)))
            channel.close()
            return

        # Get bound address for SOCKS reply
        try:
            bound = target_sock.getsockname()
            bind_host, bind_port = bound[0], bound[1]
        except Exception:
            bind_host, bind_port = '0.0.0.0', 0

        self._logger.info('Connected to %s:%d (bound %s:%d)',
                          host, port, bind_host, bind_port)

        # Create and register connection
        conn = _RelayConnection(rid, ch, channel, target_sock,
                                self._logger, self._config)
        with self._connections_lock:
            self._connections[ch] = conn

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
        self._logger.debug('Cleaned up connection ch=%d', ch)


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
        try:
            while not self._stop_event.is_set():
                try:
                    data = self.channel.read(
                        self._config.socks_relay_buffer_size,
                        timeout=self._config.socks_relay_channel_timeout
                    )
                except Exception as e:
                    if not self._stop_event.is_set():
                        self._logger.debug('Channel read error (ch=%d): %s', self.ch, e)
                    break

                if data is None:
                    # Timeout, check stop event and retry
                    continue
                if data == b'':
                    # EOF from channel
                    self._logger.debug('Channel EOF (ch=%d)', self.ch)
                    break

                try:
                    self.sock.sendall(data)
                except Exception as e:
                    if not self._stop_event.is_set():
                        self._logger.debug('Target send error (ch=%d): %s', self.ch, e)
                    break
        finally:
            self._stop_event.set()

    def _relay_target_to_channel(self):
        """Relay data from target socket to channel."""
        try:
            self.sock.settimeout(self._config.socks_relay_socket_timeout)
            while not self._stop_event.is_set():
                try:
                    data = self.sock.recv(self._config.socks_relay_buffer_size)
                except socket.timeout:
                    # Timeout, check stop event and retry
                    continue
                except Exception as e:
                    if not self._stop_event.is_set():
                        self._logger.debug('Target recv error (ch=%d): %s', self.ch, e)
                    break

                if not data:
                    # EOF from target
                    self._logger.debug('Target EOF (ch=%d)', self.ch)
                    break

                try:
                    self.channel.write_all(
                        data, timeout=self._config.socks_relay_write_timeout
                    )
                except Exception as e:
                    if not self._stop_event.is_set():
                        self._logger.debug('Channel write error (ch=%d): %s', self.ch, e)
                    break
        finally:
            self._stop_event.set()

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
