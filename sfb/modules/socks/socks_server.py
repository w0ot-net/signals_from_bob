# -*- coding: ascii -*-
"""
SOCKS server module (runs on Bob).

Accepts SOCKS5 clients on a local TCP port and proxies their
connections through the tunnel to Alice.
"""

from __future__ import absolute_import

import logging
import socket
import struct
import threading

from ..base_module import BaseModule, ModuleError
from ...logging_util import log_event
from ... import time_provider
from ..relay_connection import RelayConnection
from .socks_control_messages import T_SOCK, sock_connect
from ..relay_logging import (
    add_fields,
    duration_secs,
    relay_fields,
)


# SOCKS5 constants
SOCKS5_VERSION = 0x05
SOCKS5_AUTH_NONE = 0x00
SOCKS5_AUTH_NO_ACCEPTABLE = 0xFF
SOCKS5_CMD_CONNECT = 0x01
SOCKS5_ATYP_IPV4 = 0x01
SOCKS5_ATYP_DOMAIN = 0x03
SOCKS5_ATYP_IPV6 = 0x04

# SOCKS5 reply codes
SOCKS5_REP_SUCCESS = 0x00
SOCKS5_REP_GENERAL_FAILURE = 0x01
SOCKS5_REP_NOT_ALLOWED = 0x02
SOCKS5_REP_NET_UNREACHABLE = 0x03
SOCKS5_REP_HOST_UNREACHABLE = 0x04
SOCKS5_REP_REFUSED = 0x05
SOCKS5_REP_TTL_EXPIRED = 0x06
SOCKS5_REP_CMD_NOT_SUPPORTED = 0x07
SOCKS5_REP_ADDR_NOT_SUPPORTED = 0x08

# Map error codes to SOCKS5 reply codes
ERROR_TO_SOCKS5 = {
    'ok': SOCKS5_REP_SUCCESS,
    'general': SOCKS5_REP_GENERAL_FAILURE,
    'denied': SOCKS5_REP_NOT_ALLOWED,
    'unreachable_net': SOCKS5_REP_NET_UNREACHABLE,
    'unreachable_host': SOCKS5_REP_HOST_UNREACHABLE,
    'refused': SOCKS5_REP_REFUSED,
    'timeout': SOCKS5_REP_TTL_EXPIRED,
    'unsupported_cmd': SOCKS5_REP_CMD_NOT_SUPPORTED,
    'unsupported_addr': SOCKS5_REP_ADDR_NOT_SUPPORTED,
}


class Socks5Error(Exception):
    """SOCKS5 protocol error."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class _PendingConnect(object):
    """Tracks a pending connect request awaiting response."""

    __slots__ = ('event', 'error', 'bind_host', 'bind_port')

    def __init__(self):
        self.event = threading.Event()
        self.error = None
        self.bind_host = None
        self.bind_port = None


class SocksServerModule(BaseModule):
    """
    SOCKS5 proxy server module.

    Accepts SOCKS5 clients on a local TCP port and proxies their
    connections through the tunnel to the relay module on the peer.
    """

    TYPE = T_SOCK
    DEFAULT_COMMAND = 'start'
    REQUIRES_COMMAND = True
    REMOTE_MODULE = 'socks_relay'

    @classmethod
    def register_commands(cls, subparsers, role, config=None):
        """Register CLI subcommands for SOCKS server."""
        host_default = '0.0.0.0'
        port_default = 1080
        if config is not None:
            host_default = config.relay_listen_host
            port_default = config.relay_listen_port
        start_p = subparsers.add_parser('start', help='Start SOCKS5 proxy server')
        start_p.add_argument(
            '--socks-host', default=host_default,
            help='SOCKS server listen address (default: %s)' % host_default
        )
        start_p.add_argument(
            '--socks-port', type=int, default=port_default,
            help='SOCKS server listen port (default: %s)' % port_default
        )

    @classmethod
    def run_command(cls, args, tunnel, logger):
        """Start the SOCKS server and run until tunnel closes."""
        module = cls(tunnel, logger=logger)
        try:
            host = getattr(args, 'socks_host', None)
            port = getattr(args, 'socks_port', None)
            if host is None:
                host = module._config.relay_listen_host
            if port is None:
                port = module._config.relay_listen_port
            module.start(listen_addr=host, listen_port=port)

            # Wait for tunnel to close
            while tunnel.connected:
                time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)
            return 0
        finally:
            module.shutdown()

    def __init__(self, tunnel, logger=None):
        super(SocksServerModule, self).__init__(tunnel, logger=logger)
        self._config = tunnel._config

        # TCP server
        self._server_socket = None
        self._accept_thread = None
        self._running = False

        # Connection tracking
        self._connections = {}  # rid -> _ServerConnection
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

    def start(self, listen_addr=None, listen_port=None):
        """
        Start the SOCKS5 server.

        Args:
            listen_addr: Address to listen on
            listen_port: Port to listen on
        """
        if self._running:
            raise ModuleError('already_running', 'SOCKS server already running')

        if listen_addr is None:
            listen_addr = self._config.relay_listen_host
        if listen_port is None:
            listen_port = self._config.relay_listen_port

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((listen_addr, listen_port))
        self._server_socket.listen(self._config.relay_listen_backlog)

        self._running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name='socks-accept',
        )
        self._accept_thread.daemon = True
        self._accept_thread.start()

        log_event(
            self._logger,
            logging.INFO,
            'sock.server_listen',
            'SOCKS5 server listening (host=%s port=%d backlog=%d)' % (
                listen_addr,
                listen_port,
                self._config.relay_listen_backlog,
            ),
            lambda: add_fields(relay_fields(
                side='bob',
                peer='client',
            ), {
                'host': listen_addr,
                'port': listen_port,
                'backlog': self._config.relay_listen_backlog,
            }),
        )

    def stop(self):
        """Stop the SOCKS5 server."""
        self._running = False

        # Close server socket to unblock accept
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        # Wait for accept thread
        if self._accept_thread:
            self._accept_thread.join(timeout=self._config.relay_thread_join_timeout)

        # Stop all connections
        with self._connections_lock:
            connections = list(self._connections.values())

        for conn in connections:
            conn.stop()

        log_event(
            self._logger,
            logging.INFO,
            'sock.server_stop',
            'SOCKS5 server stopped',
            lambda: add_fields(relay_fields(
                side='bob',
                peer='client',
            ), {
                'connections': self._connection_count(),
                'pending': self._pending_count(),
            }),
        )

    def shutdown(self):
        """Stop module and clean up."""
        self.stop()
        super(SocksServerModule, self).shutdown()

    def _alloc_rid(self):
        """Allocate a unique request ID."""
        with self._rid_lock:
            rid = self._next_rid
            self._next_rid += 1
            next_rid = self._next_rid
        log_event(
            self._logger,
            logging.DEBUG,
            'sock.server_rid_alloc',
            'Allocated SOCKS request id',
            lambda: add_fields(relay_fields(
                rid=rid,
                side='bob',
                peer='client',
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
                    'sock.server_accept',
                    'Accepted connection',
                    lambda: add_fields(relay_fields(
                        side='bob',
                        peer='client',
                    ), {
                        'host': addr[0],
                        'port': addr[1],
                    }),
                )

                # Spawn handler thread
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    name='socks-client-%s:%d' % addr,
                )
                t.daemon = True
                t.start()

            except Exception as e:
                if self._running:
                    log_event(
                        self._logger,
                        logging.ERROR,
                        'sock.server_accept_error',
                        'Accept error',
                        lambda: add_fields(relay_fields(
                            side='bob',
                            peer='client',
                        ), {'error': str(e)}),
                        exc_info=True,
                    )
                    time_provider.sleep(backoff)
                    backoff = min(backoff * 2.0, max_backoff)

    def _handle_client(self, sock, addr):
        """Handle a single SOCKS5 client connection."""
        rid = self._alloc_rid()
        channel = None
        conn = None
        pending = None
        host = None
        port = None
        ch_id = None
        cleanup_reason = 'unknown'
        connect_result = None
        connect_error = None
        handshake_start = time_provider.now()
        method_time = None
        request_time = None
        channel_wait_time = None
        connect_latency = None
        connect_request_time = None

        try:
            sock.settimeout(self._config.relay_socket_timeout)
            # SOCKS5 handshake
            method_start = time_provider.now()
            self._socks5_negotiate_method(sock)
            method_time = duration_secs(method_start)
            request_start = time_provider.now()
            host, port = self._socks5_read_connect(sock)
            request_time = duration_secs(request_start)

            log_event(
                self._logger,
                logging.INFO,
                'sock.server_connect',
                'SOCKS connect requested',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    side='bob',
                    peer='client',
                ), {
                    'host': host,
                    'port': port,
                    'client_host': addr[0],
                    'client_port': addr[1],
                }),
            )

            # Open tunnel channel
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
                    'sock.server_channel_failed',
                    'Channel open failed',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=ch_id,
                        side='bob',
                        peer='client',
                    ), {'host': host, 'port': port}),
                )
                self._socks5_send_reply(sock, SOCKS5_REP_GENERAL_FAILURE)
                channel.close()
                return
            channel_wait_time = duration_secs(channel_wait_start)

            # Create connection tracker
            conn = RelayConnection(
                rid, channel.id, channel, sock, self._logger, self._config,
                side='bob',
                peer_label='Client',
                socket_to_channel_label='client_to_channel',
                channel_to_socket_label='channel_to_client',
                thread_names=(
                    'socks-rid%d-c2ch' % rid,
                    'socks-rid%d-ch2c' % rid,
                ),
                socket_eof_callback=lambda: channel.abort(
                    code='client_closed',
                    message='SOCKS client disconnected',
                ),
                close_on_socket_error=True,
            )
            with self._connections_lock:
                self._connections[rid] = conn

            # Register pending and send connect request
            pending = _PendingConnect()
            with self._pending_lock:
                self._pending[rid] = pending
                pending_count = len(self._pending)
            log_event(
                self._logger,
                logging.DEBUG,
                'sock.server_pending_add',
                'SOCKS connect pending',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='client',
                ), {'pending': pending_count}),
            )

            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_send',
                'SOCKS connect send',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='client',
                ), {'host': host, 'port': port}),
            )
            connect_request_time = time_provider.now()
            self.send_message(sock_connect(rid, channel.id, host, port))

            # Wait for response
            if not pending.event.wait(timeout=self._config.relay_connect_timeout):
                connect_latency = duration_secs(connect_request_time)
                cleanup_reason = 'connect_timeout'
                connect_result = 'timeout'
                log_event(
                    self._logger,
                    logging.WARNING,
                    'sock.server_connect_timeout',
                    'Connect timeout',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='client',
                    ), {'host': host, 'port': port}),
                )
                self._socks5_send_reply(sock, SOCKS5_REP_TTL_EXPIRED)
                return

            connect_latency = duration_secs(connect_request_time)
            if pending.error:
                error_code = ERROR_TO_SOCKS5.get(pending.error, SOCKS5_REP_GENERAL_FAILURE)
                cleanup_reason = 'connect_failed'
                connect_result = 'error'
                connect_error = pending.error
                log_event(
                    self._logger,
                    logging.INFO,
                    'sock.server_connect_failed',
                    'Connect failed',
                    lambda: add_fields(relay_fields(
                        rid=rid,
                        ch=channel.id,
                        side='bob',
                        peer='client',
                    ), {'host': host, 'port': port, 'error': pending.error}),
                )
                self._socks5_send_reply(sock, error_code)
                return

            # Send success reply
            bind_host = pending.bind_host or '0.0.0.0'
            bind_port = pending.bind_port or 0
            self._socks5_send_reply(sock, SOCKS5_REP_SUCCESS, bind_host, bind_port)
            connect_result = 'ok'

            log_event(
                self._logger,
                logging.INFO,
                'sock.server_connected',
                'Connected',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=channel.id,
                    side='bob',
                    peer='client',
                ), {'host': host, 'port': port, 'bhost': bind_host, 'bport': bind_port}),
            )

            # Start relay and wait for completion
            conn.start_relay()
            conn.wait()
            cleanup_reason = 'relay_complete'

        except Socks5Error as e:
            cleanup_reason = 'socks5_error'
            connect_result = 'protocol_error'
            connect_error = str(e)
            log_event(
                self._logger,
                logging.WARNING,
                'sock.server_error',
                'SOCKS5 error',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='client',
                ), {'error': str(e)}),
            )
        except Exception as e:
            cleanup_reason = 'client_handler_error'
            connect_result = 'handler_error'
            connect_error = str(e)
            log_event(
                self._logger,
                logging.ERROR,
                'sock.server_client_error',
                'Client handler error',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='client',
                ), {'error': str(e)}),
                exc_info=True,
            )
        finally:
            log_event(
                self._logger,
                logging.INFO,
                'sock.server_handshake',
                'SOCKS server handshake',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch_id,
                    side='bob',
                    peer='client',
                ), {
                    'host': host,
                    'port': port,
                    'client_host': addr[0],
                    'client_port': addr[1],
                    'method_time': method_time,
                    'request_time': request_time,
                    'channel_wait_time': channel_wait_time,
                    'connect_latency': connect_latency,
                    'handshake_time': duration_secs(handshake_start),
                    'connect_result': connect_result,
                    'connect_error': connect_error,
                }),
            )
            self._cleanup_connection(rid, reason=cleanup_reason)

    def _cleanup_connection(self, rid, reason=None):
        """Clean up connection resources."""
        pending_removed = False
        # Remove from pending
        with self._pending_lock:
            if rid in self._pending:
                pending_removed = True
            self._pending.pop(rid, None)

        # Remove from connections
        with self._connections_lock:
            conn = self._connections.pop(rid, None)

        if conn:
            conn.stop()
            log_event(
                self._logger,
                logging.DEBUG,
                'sock.server_cleanup',
                'Cleaned up connection',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=conn.ch,
                    side='bob',
                    peer='client',
                ), {
                    'reason': reason,
                    'pending_removed': pending_removed,
                    'connections': self._connection_count(),
                    'pending': self._pending_count(),
                }),
            )

    # --- SOCKS5 Protocol Implementation ---

    def _recv_exact(self, sock, size):
        """Receive exactly size bytes from socket."""
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise Socks5Error('closed', 'connection closed')
            data.extend(chunk)
        return bytes(data)

    def _socks5_negotiate_method(self, sock):
        """
        SOCKS5 method negotiation.

        Client sends: VER | NMETHODS | METHODS
        Server sends: VER | METHOD
        """
        # Read version and method count
        data = self._recv_exact(sock, 2)
        version, nmethods = struct.unpack('!BB', data)

        if version != SOCKS5_VERSION:
            raise Socks5Error('version', 'SOCKS version %d not supported' % version)

        if nmethods == 0:
            raise Socks5Error('no_methods', 'no methods offered')

        # Read methods
        methods = self._recv_exact(sock, nmethods)

        # Check for NO AUTH (0x00)
        if SOCKS5_AUTH_NONE not in methods:
            # Send rejection
            sock.sendall(struct.pack('!BB', SOCKS5_VERSION, SOCKS5_AUTH_NO_ACCEPTABLE))
            raise Socks5Error('auth', 'no acceptable auth method')

        # Accept NO AUTH
        sock.sendall(struct.pack('!BB', SOCKS5_VERSION, SOCKS5_AUTH_NONE))

    def _socks5_read_connect(self, sock):
        """
        Read SOCKS5 CONNECT request.

        Client sends: VER | CMD | RSV | ATYP | DST.ADDR | DST.PORT

        Returns:
            tuple: (host, port)
        """
        # Read header
        data = self._recv_exact(sock, 4)
        version, cmd, rsv, atyp = struct.unpack('!BBBB', data)

        if version != SOCKS5_VERSION:
            raise Socks5Error('version', 'bad version in request')

        if cmd != SOCKS5_CMD_CONNECT:
            self._socks5_send_reply(sock, SOCKS5_REP_CMD_NOT_SUPPORTED)
            raise Socks5Error('command', 'only CONNECT supported')

        # Parse address
        if atyp == SOCKS5_ATYP_IPV4:
            addr_data = self._recv_exact(sock, 4)
            host = socket.inet_ntoa(addr_data)
        elif atyp == SOCKS5_ATYP_DOMAIN:
            len_byte = self._recv_exact(sock, 1)
            name_len = struct.unpack('!B', len_byte)[0]
            host = self._recv_exact(sock, name_len).decode('ascii')
        elif atyp == SOCKS5_ATYP_IPV6:
            self._socks5_send_reply(sock, SOCKS5_REP_ADDR_NOT_SUPPORTED)
            raise Socks5Error('address', 'IPv6 not supported')
        else:
            self._socks5_send_reply(sock, SOCKS5_REP_ADDR_NOT_SUPPORTED)
            raise Socks5Error('address', 'address type %d not supported' % atyp)

        # Read port
        port_data = self._recv_exact(sock, 2)
        port = struct.unpack('!H', port_data)[0]

        return host, port

    def _socks5_send_reply(self, sock, reply_code, bind_addr='0.0.0.0', bind_port=0):
        """
        Send SOCKS5 reply.

        VER | REP | RSV | ATYP | BND.ADDR | BND.PORT
        """
        reply = bytearray([
            SOCKS5_VERSION,
            reply_code,
            0x00,  # Reserved
            SOCKS5_ATYP_IPV4,  # Address type: IPv4
        ])
        reply.extend(socket.inet_aton(bind_addr))
        reply.extend(struct.pack('!H', bind_port))
        sock.sendall(bytes(reply))

    # --- Response Handlers ---

    def handle_connect_ok(self, msg):
        """Handle connect_ok from Alice."""
        rid = msg.get('rid')
        if rid is None:
            return

        with self._pending_lock:
            pending = self._pending.get(rid)

        if pending:
            pending.bind_host = msg.get('bhost')
            pending.bind_port = msg.get('bport')
            pending.event.set()
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_ok_recv',
                'SOCKS connect ok recv',
                lambda: add_fields(relay_fields(
                    rid=rid,
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
        rid = msg.get('rid')
        if rid is None:
            return

        with self._pending_lock:
            pending = self._pending.get(rid)

        if pending:
            pending.error = msg.get('code', 'general')
            pending.event.set()
            log_event(
                self._logger,
                logging.INFO,
                'sock.connect_err_recv',
                'SOCKS connect err recv',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=msg.get('ch'),
                    side='bob',
                    peer='client',
                ), {
                    'code': msg.get('code'),
                    'reason': msg.get('reason'),
                }),
            )
