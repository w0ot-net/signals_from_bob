# -*- coding: ascii -*-
"""
Base tunnel class with shared functionality.

Provides common infrastructure for both AliceTunnel and BobTunnel:
- Channel management
- Packet encoding/decoding
- Encryption/decryption
- State machine
- Handshake logic
"""

from __future__ import absolute_import

import logging
import threading
import time

from ..channel import ChannelManager, ChannelError
from ..config import Config
from ..crypto import Plain
from .tunnel_control_messages import (
    tun_pong,
    tun_mtu,
    tun_mtu_ok,
    tun_mtu_ack,
    tun_window,
    tun_window_ok,
    encode as encode_message,
)
from ..protocol import (
    Packet,
    PacketHeader,
    Segment,
    FLAG_SYN,
    FLAG_ACK,
    PACKET_HEADER_SIZE,
)
from ..reliability import SendWindow, RecvWindow, ReliabilityStats, NoopReliabilityStats
from ..logging_util import log_event


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


class TunnelState(object):
    """Tunnel connection states."""
    DISCONNECTED = 'disconnected'
    CONNECTING = 'connecting'
    CONNECTED = 'connected'
    CLOSING = 'closing'
    CLOSED = 'closed'


class TunnelError(Exception):
    """Tunnel operation error."""
    pass


class BaseTunnel(object):
    """
    Base tunnel with shared functionality.

    Subclasses (AliceTunnel, BobTunnel) implement transport-specific
    send/receive patterns.
    """

    # Reserved message types - cannot be overridden by modules
    RESERVED_TYPES = frozenset(['tun', 'ch'])

    # Pre-negotiation limits
    MAX_WINDOW = 64  # SACK bitmap size limit

    def __init__(self, config, crypto=None, is_initiator=True, logger=None):
        """
        Initialize the tunnel.

        Args:
            config: Config instance with tunnel settings
            crypto: Cipher instance (default: Plain)
            is_initiator: True if this side initiates handshake (Alice)
            logger: Optional logger instance
        """
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')

        self._config = config
        self._crypto = crypto if crypto is not None else Plain()
        self._is_initiator = is_initiator
        self._state = TunnelState.DISCONNECTED
        self._logger = logger or logging.getLogger(__name__)
        self._payload_cap = None

        # Channel management
        self._channel_manager = ChannelManager(is_alice=is_initiator, config=config)

        # Initial MTU/window before negotiation
        self._default_mtu = config.protocol_initial_mtu
        self._default_window = config.tunnel_initial_window

        # Reliability - start with initial window until negotiated
        self._proposed_max_in_flight = min(config.tunnel_max_in_flight, self.MAX_WINDOW)
        if config.tunnel_stats_enabled:
            self._reliability_stats = ReliabilityStats()
            self._stats_enabled = True
        else:
            self._reliability_stats = NoopReliabilityStats()
            self._stats_enabled = False
        self._send_window = SendWindow(
            max_in_flight=self._default_window,
            stats=self._reliability_stats,
        )
        self._recv_window = RecvWindow(
            max_buffer=self._proposed_max_in_flight,
            stats=self._reliability_stats,
        )

        # Sequence numbers
        self._local_isn = None  # Set during handshake
        self._remote_isn = None  # Set during handshake

        # MTU/Window negotiation state (asymmetric)
        self._proposed_send_mtu = None  # Set by subclass from transport
        self._proposed_recv_mtu = None  # Set by subclass from transport
        self._negotiated_send_mtu = self._default_mtu
        self._negotiated_recv_mtu = self._default_mtu
        self._send_mtu = self._default_mtu  # Sender payload MTU
        self._recv_mtu = self._default_mtu  # Receiver payload MTU
        self._pending_send_mtu = None
        self._negotiated_window = self._default_window
        self._mtu_negotiated = False
        self._window_negotiated = False

        # Module handlers for control message dispatch
        # Maps message type (t field) to handler callable
        self._module_handlers = {}
        self._module_loader = None

        # Statistics
        self._packets_sent = 0
        self._packets_received = 0
        self._bytes_sent = 0
        self._bytes_received = 0

        # Transport MTU for receive (payload + header)
        self._max_packet_size = self._default_mtu + PACKET_HEADER_SIZE

        # Background thread support
        self._bg_thread = None
        self._bg_stop = False

    @property
    def state(self):
        """Current tunnel state."""
        return self._state

    @property
    def connected(self):
        """True if tunnel is connected."""
        return self._state == TunnelState.CONNECTED

    @property
    def is_connected(self):
        """True if tunnel is connected (alias for connected)."""
        return self._state == TunnelState.CONNECTED

    @property
    def channel_manager(self):
        """Channel manager for this tunnel."""
        return self._channel_manager

    @property
    def control(self):
        """Control channel (channel 0)."""
        return self._channel_manager.control

    @property
    def module_loader(self):
        """Module loader instance if enabled."""
        return self._module_loader

    @property
    def negotiated_mtu(self):
        """Current effective MTUs as (send_mtu, recv_mtu)."""
        return (self._negotiated_send_mtu, self._negotiated_recv_mtu)

    @property
    def negotiated_send_mtu(self):
        """Current negotiated send MTU (payload bytes)."""
        return self._negotiated_send_mtu

    @property
    def negotiated_recv_mtu(self):
        """Current negotiated receive MTU (payload bytes)."""
        return self._negotiated_recv_mtu

    @property
    def negotiated_window(self):
        """Current effective window size (1 until negotiated)."""
        return self._negotiated_window

    @property
    def reliability_stats(self):
        """Reliability stats if enabled, otherwise None."""
        if not self._stats_enabled:
            return None
        return self._reliability_stats

    def _set_state(self, new_state):
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._logger.debug('State: %s -> %s', old_state, new_state)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.state',
            'Tunnel state change',
            {'from': old_state, 'to': new_state},
        )

    def _generate_isn(self):
        """Generate initial sequence number."""
        return 1

    def _encrypt(self, data):
        """Encrypt data using configured cipher."""
        return self._crypto.encrypt(data)

    def _decrypt(self, data):
        """Decrypt data using configured cipher."""
        return self._crypto.decrypt(data)

    def _build_packet(self, flags=0, segments=None):
        """
        Build a packet with current seq/ack state.

        Args:
            flags: Packet flags (FLAG_SYN, FLAG_ACK)
            segments: List of Segment instances

        Returns:
            tuple: (Packet, seq) where seq is the sequence number used
        """
        seq = self._send_window.next_seq
        ack = self._recv_window.ack
        sack = self._recv_window.sack

        packet = Packet(seq=seq, ack=ack, sack=sack, flags=flags)
        if segments:
            for seg in segments:
                packet.add_segment(seg)

        return packet, seq

    def _rebuild_packet(self, seq, segments):
        """
        Rebuild a packet with specific seq and fresh ack/sack.

        Used for retransmission to ensure current ACK state is sent.

        Args:
            seq: Sequence number to use (from original send)
            segments: List of Segment instances (from original send)

        Returns:
            Packet: Rebuilt packet with fresh ack/sack
        """
        ack = self._recv_window.ack
        sack = self._recv_window.sack

        packet = Packet(seq=seq, ack=ack, sack=sack, flags=0)
        if segments:
            for seg in segments:
                packet.add_segment(seg)

        return packet

    def _encode_packet(self, packet):
        """Encode and encrypt a packet."""
        raw = packet.encode()
        return self._encrypt(raw)

    def _decode_packet(self, data, max_size=None):
        """
        Decrypt and decode a packet.

        Args:
            data: Encrypted packet bytes
            max_size: Optional max packet size

        Returns:
            Packet instance or None on error
        """
        if max_size is None:
            max_size = self._max_packet_size
        try:
            decrypted = self._decrypt(data)
            return Packet.decode(decrypted, max_size=max_size)
        except (ValueError, TypeError) as e:
            self._logger.warning('Failed to decode packet: %s', e)
            return None

    def _process_incoming_packet(self, packet, now=None):
        """
        Process an incoming packet.

        - Updates send_window with ACK/SACK from peer
        - Passes packet through recv_window for ordering/dedup
        - Delivers in-order segments to channels
        - Processes control messages

        Args:
            packet: Decoded Packet instance
            now: Current time (default: time.time())

        Returns:
            list: RTT samples from ACKed packets
        """
        if now is None:
            now = time.time()

        # Process ACK/SACK from peer (updates our send window)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_recv',
            'Packet received',
            {
                'seq': packet.seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(packet.segments),
                'bytes': packet.encoded_size(),
            },
        )

        unacked_before = len(self._send_window._unacked)
        rtt_samples = self._send_window.process_ack(
            packet.ack, packet.sack, now=now
        )
        unacked_after = len(self._send_window._unacked)
        if unacked_before != unacked_after or unacked_after > 0:
            self._logger.debug('ACK=%d SACK=0x%x: unacked %d->%d',
                              packet.ack, packet.sack, unacked_before, unacked_after)
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.ack',
                'ACK processed',
                {
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'unacked_before': unacked_before,
                    'unacked_after': unacked_after,
                },
            )

        # Pass through recv_window for ordering and deduplication
        # recv_window.receive() returns list of (seq, packet) ready for delivery
        ready_packets = self._recv_window.receive(packet.seq, packet)
        self._logger.debug('recv_window returned %d ready packets for seq=%d',
                          len(ready_packets), packet.seq)

        # Deliver segments from in-order packets only
        for seq, ready_packet in ready_packets:
            self._logger.debug('Delivering %d segments from seq=%d',
                              len(ready_packet.segments), seq)
            for segment in ready_packet.segments:
                self._channel_manager.deliver_segment(segment)

        # Process control messages
        self._process_control_messages()

        self._packets_received += 1

        return rtt_samples

    def _process_control_messages(self):
        """Process pending control messages from channel 0."""
        ctrl = self._channel_manager.control
        count = 0
        while True:
            try:
                msg = ctrl.recv_message(timeout=0)
                if msg is None:
                    break
                count += 1
                self._logger.debug('Dispatching control msg: %s', msg)
                self._dispatch_control_message(msg)
            except ChannelError as e:
                # Invalid JSON - log and drop
                self._logger.warning('Invalid control message: %s', e)
                break
        if count > 0:
            self._logger.debug('Processed %d control messages', count)

    def register_module(self, type_code, handler):
        """
        Register a module to handle control messages of a given type.

        Args:
            type_code: Message type (t field), e.g., 'file', 'sh', 'sock'
            handler: Callable that takes a message dict

        Raises:
            ValueError: If type_code is reserved or already registered
        """
        if type_code in self.RESERVED_TYPES:
            raise ValueError('Cannot register reserved type: %s' % type_code)
        if type_code in self._module_handlers:
            raise ValueError('Handler already registered: %s' % type_code)
        self._module_handlers[type_code] = handler
        self._logger.debug('Registered module handler: %s', type_code)

    def unregister_module(self, type_code):
        """
        Unregister a module handler.

        Args:
            type_code: Message type to unregister

        Returns:
            bool: True if handler was removed, False if not found
        """
        if type_code in self._module_handlers:
            del self._module_handlers[type_code]
            self._logger.debug('Unregistered module handler: %s', type_code)
            return True
        return False

    def _dispatch_control_message(self, msg):
        """
        Dispatch a control message to the appropriate handler.

        Message format: {"t": "<type>", "c": "<command>", ...}
        - t: Message type (tun, ch, or module type)
        - c: Command within that type

        Dispatch order:
        1. t="tun" -> tunnel handles (ping/pong, negotiation)
        2. t="ch"  -> channel_manager handles (open/close)
        3. t=other -> registered module handler
        4. Unknown -> log and drop

        Args:
            msg: Decoded JSON object
        """
        msg_type = msg.get('t')
        cmd = msg.get('c')

        if not msg_type or not cmd:
            self._logger.debug('Invalid control message: missing t or c')
            return

        if msg_type == 'tun':
            self._handle_tunnel_message(cmd, msg)
        elif msg_type == 'ch':
            self._handle_channel_message(cmd, msg)
        elif msg_type in self._module_handlers:
            try:
                self._module_handlers[msg_type](msg)
            except Exception as e:
                self._logger.warning('Module %s error: %s', msg_type, e)
        else:
            self._logger.debug('Unknown message type: %s', msg_type)

    def _handle_tunnel_message(self, cmd, msg):
        """
        Handle tunnel-level control messages (t="tun").

        Commands: ping, pong, mtu, mtu_ok, window, window_ok
        """
        self._logger.debug('_handle_tunnel_message: cmd=%s', cmd)
        if cmd == 'ping':
            self._handle_ping(msg)
        elif cmd == 'pong':
            # pong confirms peer is alive - nothing else to do
            pass
        elif cmd == 'mtu':
            self._handle_mtu(msg)
        elif cmd == 'mtu_ok':
            self._handle_mtu_ok(msg)
        elif cmd == 'mtu_ack':
            self._handle_mtu_ack(msg)
        elif cmd == 'window':
            self._handle_window(msg)
        elif cmd == 'window_ok':
            self._handle_window_ok(msg)
        else:
            self._logger.debug('Unknown tunnel command: %s', cmd)

    def _handle_channel_message(self, cmd, msg):
        """
        Handle channel-level control messages (t="ch").

        Delegates to channel_manager.
        """
        self._channel_manager.handle_control_message(msg)

    def _handle_ping(self, msg):
        """Handle ping by queueing pong."""
        if not self._channel_manager.has_pending_data():
            self.control.send_message(tun_pong())

    def _handle_mtu(self, msg):
        """
        Handle MTU negotiation request (Bob receives from Alice).

        Responds with mtu_ok containing per-direction MTUs.
        Does NOT update _send_mtu yet - waits for mtu_ack from Alice.
        """
        requested_tx = msg.get('tx', self._default_mtu)
        requested_rx = msg.get('rx', self._default_mtu)
        if (not isinstance(requested_tx, integer_types) or requested_tx < 1 or
                not isinstance(requested_rx, integer_types) or requested_rx < 1):
            self._logger.warning('Invalid MTU request: %s', msg)
            return
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_propose',
            'MTU request received',
            {'tx': requested_tx, 'rx': requested_rx},
        )

        # Negotiate each direction independently.
        agreed_recv = min(requested_tx, self._proposed_recv_mtu or self._default_mtu)
        agreed_send = min(requested_rx, self._proposed_send_mtu or self._default_mtu)

        self._negotiated_recv_mtu = agreed_recv
        self._recv_mtu = agreed_recv
        self._max_packet_size = agreed_recv + PACKET_HEADER_SIZE
        self._pending_send_mtu = agreed_send

        # Send confirmation (using small packets still)
        self.control.send_message(tun_mtu_ok(agreed_send, agreed_recv))
        self._logger.debug('MTU recv updated: %d (tx=%d rx=%d), awaiting ack',
                           agreed_recv, agreed_send, agreed_recv)
        self._logger.info('MTU negotiate: recv=%d send(pending)=%d',
                          agreed_recv, agreed_send)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ok',
            'MTU negotiate response',
            {'recv': agreed_recv, 'send_pending': agreed_send},
        )

    def _handle_mtu_ok(self, msg):
        """
        Handle MTU negotiation response (Alice receives from Bob).

        Updates negotiated send/recv MTU, then sends mtu_ack.
        """
        agreed_tx = msg.get('tx', self._default_mtu)
        agreed_rx = msg.get('rx', self._default_mtu)
        if (not isinstance(agreed_tx, integer_types) or agreed_tx < 1 or
                not isinstance(agreed_rx, integer_types) or agreed_rx < 1):
            self._logger.warning('Invalid MTU response: %s', msg)
            return

        # Clamp to our transport limits.
        agreed_send = min(agreed_rx, self._proposed_send_mtu or self._default_mtu)
        agreed_recv = min(agreed_tx, self._proposed_recv_mtu or self._default_mtu)

        self._negotiated_send_mtu = agreed_send
        self._negotiated_recv_mtu = agreed_recv
        self._send_mtu = agreed_send
        self._recv_mtu = agreed_recv
        self._max_packet_size = agreed_recv + PACKET_HEADER_SIZE
        self._mtu_negotiated = True

        # Send ack so Bob knows he can also start sending larger packets
        self.control.send_message(tun_mtu_ack())
        self._logger.debug('MTU negotiated: tx=%d rx=%d, sent ack',
                           agreed_send, agreed_recv)
        self._logger.info('MTU negotiated: send=%d recv=%d',
                          agreed_send, agreed_recv)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ok',
            'MTU negotiated',
            {'send': agreed_send, 'recv': agreed_recv},
        )

    def _handle_mtu_ack(self, msg):
        """
        Handle MTU negotiation acknowledgment (Bob receives from Alice).

        Now Bob can safely use the larger MTU for sending.
        """
        if self._pending_send_mtu is not None:
            self._send_mtu = self._pending_send_mtu
            self._negotiated_send_mtu = self._pending_send_mtu
            self._pending_send_mtu = None
        self._mtu_negotiated = True
        self._logger.debug('MTU ack received, send MTU now: %d', self._send_mtu)
        self._logger.info('MTU ack applied: send=%d recv=%d',
                          self._send_mtu, self._recv_mtu)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ack',
            'MTU ack applied',
            {'send': self._send_mtu, 'recv': self._recv_mtu},
        )

    def _handle_window(self, msg):
        """
        Handle window negotiation request (Bob receives from Alice).

        Responds with window_ok containing min(requested, our_max, 64).
        """
        self._logger.debug('RECV window request: %s', msg)
        requested = msg.get('size', self._default_window)
        if not isinstance(requested, integer_types) or requested < 1:
            self._logger.warning('Invalid window request: %s', requested)
            return
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window request received',
            {'size': requested},
        )

        # Negotiate: use minimum of requested, our proposed, and max (64)
        agreed = min(requested, self._proposed_max_in_flight, self.MAX_WINDOW)
        self._negotiated_window = agreed
        self._window_negotiated = True

        # Update send window limit
        self._send_window._max_in_flight = agreed

        # Send confirmation
        self.control.send_message(tun_window_ok(agreed))
        self._logger.debug('SEND window_ok: %d (requested %d)', agreed, requested)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_ok',
            'Window negotiated',
            {'requested': requested, 'agreed': agreed},
        )

    def _handle_window_ok(self, msg):
        """
        Handle window negotiation response (Alice receives from Bob).

        Updates negotiated_window and send_window limit.
        """
        self._logger.debug('RECV window_ok: %s', msg)
        agreed = msg.get('size', self._default_window)
        if not isinstance(agreed, integer_types) or agreed < 1:
            self._logger.warning('Invalid window response: %s', agreed)
            return

        self._negotiated_window = agreed
        self._window_negotiated = True

        # Update send window limit
        self._send_window._max_in_flight = agreed
        self._logger.debug('Window updated to: %d', agreed)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_ok',
            'Window updated',
            {'agreed': agreed},
        )

    def _collect_segments(self, max_payload, keepalive_data=None):
        """
        Collect segments from channels for transmission.

        Args:
            max_payload: Max bytes for segments
            keepalive_data: Optional keepalive bytes if no data

        Returns:
            list: List of Segment instances
        """
        if self._payload_cap:
            cap_payload = self._payload_cap - PACKET_HEADER_SIZE
            if cap_payload < 0:
                cap_payload = 0
            if max_payload > cap_payload:
                max_payload = cap_payload
        return self._channel_manager.collect_segments(
            max_payload, keepalive_data=keepalive_data
        )

    def start_background(self):
        """
        Start the tunnel's message loop in a background thread.

        The loop runs until stop_background() is called or the tunnel closes.
        Subclasses must implement _run_loop() to define their specific loop.
        """
        if self._bg_thread is not None:
            return  # Already running

        self._bg_stop = False
        self._bg_thread = threading.Thread(target=self._bg_run)
        self._bg_thread.daemon = True
        self._bg_thread.start()

    def stop_background(self, timeout=None):
        """
        Stop the background thread.

        Args:
            timeout: Max seconds to wait for thread to finish
        """
        self._bg_stop = True
        if timeout is None:
            timeout = self._config.tunnel_bg_stop_timeout
        if self._bg_thread is not None:
            self._bg_thread.join(timeout=timeout)
            self._bg_thread = None

    def _bg_run(self):
        """Background thread entry point."""
        try:
            self._run_loop()
        except Exception as e:
            if not self._bg_stop:
                self._logger.exception('Background loop error: %s', e)

    def _run_loop(self):
        """
        Subclass-specific message processing loop.

        Subclasses must implement this to define their loop behavior.
        The loop should check self._bg_stop and self._state to know when to exit.
        """
        raise NotImplementedError('Subclass must implement _run_loop()')

    def close(self):
        """Close the tunnel and stop background thread."""
        if self._state == TunnelState.CLOSED:
            return

        self.stop_background()
        if self._module_loader is not None:
            self._module_loader.shutdown()
        self._set_state(TunnelState.CLOSED)
        self._logger.info('Tunnel closed')

    def enable_module_loader(self, logger=None):
        """Enable and return the module loader service."""
        if self._module_loader is not None:
            return self._module_loader
        from .module_loader import ModuleLoader
        self._module_loader = ModuleLoader(self, logger=logger or self._logger)
        return self._module_loader
