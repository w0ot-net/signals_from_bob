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

from ..channel import ChannelManager, ChannelError
from ..compat import integer_types
from ..config import Config
from ..crypto import Plain
from .tunnel_control_messages import (
    tun_mtu,
    tun_mtu_ok,
    tun_mtu_ack,
    tun_window,
    tun_window_ok,
)
from ..protocol import (
    Packet,
    PacketHeader,
    Segment,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
    FLAG_POLL_HINT,
    PACKET_HEADER_SIZE,
    log_control_segments,
)
from ..reliability import SendWindow, RecvWindow, ReliabilityStats, NoopReliabilityStats
from ..logging_util import get_logger, log_event
from .. import time_provider

class TunnelState(object):
    """Tunnel connection states."""
    DISCONNECTED = 'disconnected'
    CONNECTING = 'connecting'
    HANDSHAKE_ACKED = 'handshake_acked'
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
    MAX_WINDOW = 256  # SACK bitmap size limit

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
        self._logger = logger or get_logger(__name__)

        # Channel management
        self._channel_manager = ChannelManager(is_alice=is_initiator, config=config)

        # Initial MTU/window before negotiation
        self._default_packet_mtu = config.protocol_initial_packet_mtu
        self._default_window = config.tunnel_initial_window

        # Reliability - start with initial window until negotiated
        self._proposed_window = min(config.max_in_flight, self.MAX_WINDOW)
        if config.stats_enabled:
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
            max_buffer=self._proposed_window,
            stats=self._reliability_stats,
        )

        # Sequence numbers
        self._local_isn = None  # Set during handshake
        self._remote_isn = None  # Set during handshake

        # MTU/Window negotiation state (asymmetric)
        self._proposed_send_packet_mtu = None  # Set by subclass from transport
        self._proposed_recv_packet_mtu = None  # Set by subclass from transport
        self._send_packet_mtu = self._default_packet_mtu
        self._recv_packet_mtu = self._default_packet_mtu
        self._pending_send_packet_mtu = None  # Pending send MTU increase awaiting ack
        self._mtu_negotiated = False
        self._window_negotiated = False
        self._window_final = False

        # Module handlers for control message dispatch
        # Maps message type (t field) to handler callable
        self._module_handlers = {}
        self._module_handlers_lock = threading.Lock()
        self._module_loader = None

        # Statistics
        self._packets_sent = 0
        self._packets_received = 0
        self._bytes_sent = 0
        self._bytes_received = 0

        # Transport MTU for receive (packet bytes)
        self._max_packet_size = self._recv_packet_mtu

        # Background thread support
        self._bg_thread = None
        self._bg_stop = False
        self._bg_lock = threading.Lock()

    def _init_transport_limits(self, transport):
        """
        Initialize transport-derived payload/MTU limits.
        """
        send_packet_mtu = transport.send_packet_mtu
        recv_packet_mtu = transport.recv_packet_mtu
        self._proposed_send_packet_mtu = send_packet_mtu
        self._proposed_recv_packet_mtu = recv_packet_mtu
        self._send_packet_mtu = send_packet_mtu
        self._recv_packet_mtu = recv_packet_mtu
        self._max_packet_size = recv_packet_mtu
        return (
            self._payload_mtu_from_packet(send_packet_mtu),
            self._payload_mtu_from_packet(recv_packet_mtu),
        )

    @staticmethod
    def _payload_mtu_from_packet(packet_mtu):
        payload_bytes = packet_mtu - PACKET_HEADER_SIZE
        if payload_bytes < 1:
            return 1
        return payload_bytes

    @staticmethod
    def _packet_mtu_from_payload(payload_bytes):
        return payload_bytes + PACKET_HEADER_SIZE

    @property
    def state(self):
        """Current tunnel state."""
        return self._state

    @property
    def connected(self):
        """True if tunnel is connected."""
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
    def negotiated_packet_mtu(self):
        """Current effective MTUs as (send_packet_mtu, recv_packet_mtu)."""
        return (self._send_packet_mtu, self._recv_packet_mtu)

    @property
    def negotiated_send_packet_mtu(self):
        """Current effective send MTU (packet bytes)."""
        return self._send_packet_mtu

    @property
    def negotiated_recv_packet_mtu(self):
        """Current effective receive MTU (packet bytes)."""
        return self._recv_packet_mtu

    @property
    def negotiated_window(self):
        """Current effective window size."""
        return self._send_window._max_in_flight

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
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.state',
            'Tunnel state change',
            lambda: {
                'from': old_state,
                'to': new_state,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )

    def _generate_isn(self):
        """Generate initial sequence number."""
        return 1

    def _encrypt(self, data, seq=None, direction=None):
        """Encrypt data using configured cipher."""
        return self._crypto.encrypt(data, seq=seq, direction=direction)

    def _decrypt(self, data, seq=None, direction=None):
        """Decrypt data using configured cipher."""
        return self._crypto.decrypt(data, seq=seq, direction=direction)

    def _direction_outbound(self):
        return 0 if self._is_initiator else 1

    def _direction_inbound(self):
        return 1 if self._is_initiator else 0

    @staticmethod
    def _encode_segments(segments):
        parts = []
        for seg in segments:
            parts.append(seg.encode())
        return b''.join(parts)

    def _build_packet(self, flags=0, segments=None):
        """
        Build a packet with current seq/ack state.

        Args:
            flags: Packet flags (FLAG_SYN, FLAG_ACK, FLAG_KEEPALIVE,
                FLAG_HAS_SEGMENTS)
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

    @staticmethod
    def _prefix_fields(prefix, fields):
        if not fields:
            return {}
        prefixed = {}
        for key, value in fields.items():
            prefixed[prefix + key] = value
        return prefixed

    def _reliability_snapshot(self, now=None, include_buffered=False):
        if now is None:
            now = time_provider.now()
        fields = {
            'side': 'alice' if self._is_initiator else 'bob',
            'state': self._state,
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'negotiated_window': self.negotiated_window,
            'packets_sent': self._packets_sent,
            'packets_received': self._packets_received,
            'bytes_sent': self._bytes_sent,
            'bytes_received': self._bytes_received,
        }
        send_state = self._send_window.debug_state(now=now)
        fields.update(self._prefix_fields('send_', send_state))
        recv_state = self._recv_window.debug_state(
            include_buffered=include_buffered
        )
        fields.update(self._prefix_fields('recv_', recv_state))
        if self._stats_enabled:
            try:
                stats_snapshot = self._reliability_stats.snapshot()
            except Exception:
                stats_snapshot = None
            if stats_snapshot:
                fields.update(self._prefix_fields('stat_', stats_snapshot))
        if hasattr(self, '_rtt'):
            try:
                rtt_state = self._rtt.debug_state()
            except Exception:
                rtt_state = None
            if rtt_state:
                fields.update(self._prefix_fields('rtt_', rtt_state))
        ack_silence = self._send_window.ack_silence(now=now)
        if ack_silence is not None:
            fields['ack_silence'] = round(ack_silence, 6)
        ack_progress_silence = self._send_window.ack_progress_silence(now=now)
        if ack_progress_silence is not None:
            fields['ack_progress_silence'] = round(ack_progress_silence, 6)
        if self._send_window.last_cum_ack is not None:
            fields['last_cum_ack'] = self._send_window.last_cum_ack
        return fields

    def _log_reliability_state(self, level, event, message, now=None,
                               include_buffered=False, extra_fields=None):
        def build_fields():
            fields = self._reliability_snapshot(
                now=now,
                include_buffered=include_buffered,
            )
            if extra_fields:
                fields.update(extra_fields)
            return fields
        log_event(
            self._logger,
            level,
            event,
            message,
            build_fields,
        )

    def _rebuild_packet(self, seq, segments, flags=0):
        """
        Rebuild a packet with specific seq and fresh ack/sack.

        Used for retransmission to ensure current ACK state is sent.

        Args:
            seq: Sequence number to use (from original send)
            segments: List of Segment instances (from original send)
            flags: Packet flags (from original send)

        Returns:
            Packet: Rebuilt packet with fresh ack/sack
        """
        ack = self._recv_window.ack
        sack = self._recv_window.sack

        packet = Packet(seq=seq, ack=ack, sack=sack, flags=flags)
        if segments:
            for seg in segments:
                packet.add_segment(seg)

        return packet

    def _encode_packet(self, packet, encrypted_body=None):
        """Encode and encrypt a packet."""
        header = packet.header.encode()
        if encrypted_body is None:
            body = self._encode_segments(packet.segments)
            encrypted_body = self._encrypt(
                body,
                seq=packet.seq,
                direction=self._direction_outbound(),
            )
        return header + encrypted_body

    def _encode_packet_for_send(self, packet, encrypted_body=None):
        """
        Encode a packet for sending, returning (encrypted_body, packet_data).

        This uses the outbound direction and preserves any provided
        encrypted_body to avoid re-encrypting retransmits.
        """
        if encrypted_body is None:
            body = self._encode_segments(packet.segments)
            encrypted_body = self._encrypt(
                body,
                seq=packet.seq,
                direction=self._direction_outbound(),
            )
        packet_data = self._encode_packet(packet, encrypted_body=encrypted_body)
        return encrypted_body, packet_data

    def _packet_send_fields(self, packet, data_len, context):
        """Build common tunnel.packet_send log fields."""
        content_flag = self._content_flag_label(packet.flags)
        return {
            'seq': packet.seq,
            'ack': packet.ack,
            'sack': packet.sack,
            'flags': packet.flags,
            'seg_count': len(packet.segments),
            'bytes': data_len,
            'context': context,
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'negotiated_window': self.negotiated_window,
            'unacked': self._send_window.unacked_count,
            'max_in_flight': self._send_window._max_in_flight,
            'keepalive': bool(packet.flags & FLAG_KEEPALIVE),
            'poll_hint': bool(packet.flags & FLAG_POLL_HINT),
            'content_flag': content_flag,
            'has_data': bool(packet.segments),
            'side': 'alice' if self._is_initiator else 'bob',
            'state': self._state,
        }

    @staticmethod
    def _content_flag_label(flags):
        content_flags = flags & (FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS)
        if content_flags == FLAG_HAS_SEGMENTS:
            return 'has_segments'
        if content_flags == FLAG_KEEPALIVE:
            return 'keepalive'
        if content_flags == 0:
            return 'none'
        return 'invalid'

    def _close_protocol_violation(self, reason, packet=None):
        def build_fields():
            fields = {
                'reason': reason,
                'state': self._state,
                'side': 'alice' if self._is_initiator else 'bob',
            }
            if packet is not None:
                fields.update({
                    'seq': packet.seq,
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'flags': packet.flags,
                    'seg_count': len(packet.segments),
                })
            return fields
        log_event(
            self._logger,
            logging.ERROR,
            'tunnel.protocol_violation',
            'Protocol violation',
            build_fields,
        )
        self.close()
        return False

    def _validate_content_flags(self, packet):
        flags = packet.flags
        handshake_flags = flags & (FLAG_SYN | FLAG_ACK)
        content_flags = flags & (FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS)
        poll_hint = bool(flags & FLAG_POLL_HINT)
        if handshake_flags:
            if poll_hint:
                return self._close_protocol_violation(
                    'handshake_with_poll_hint', packet
                )
            if packet.segments:
                return self._close_protocol_violation(
                    'handshake_with_segments', packet
                )
            if content_flags:
                return self._close_protocol_violation(
                    'handshake_with_content_flags', packet
                )
            return True

        if self._state == TunnelState.CONNECTING:
            return self._close_protocol_violation(
                'non_handshake_while_connecting', packet
            )

        if self._state == TunnelState.DISCONNECTED:
            return self._close_protocol_violation(
                'non_handshake_while_disconnected', packet
            )

        if self._state in (TunnelState.HANDSHAKE_ACKED, TunnelState.CONNECTED):
            if poll_hint and content_flags == 0:
                return self._close_protocol_violation(
                    'poll_hint_without_content_flags', packet
                )
            if content_flags == 0:
                return self._close_protocol_violation(
                    'missing_content_flags', packet
                )
            if content_flags & (content_flags - 1):
                return self._close_protocol_violation(
                    'multiple_content_flags', packet
                )
            if content_flags == FLAG_HAS_SEGMENTS:
                if not packet.segments:
                    return self._close_protocol_violation(
                        'has_segments_without_segments', packet
                    )
            else:
                if packet.segments:
                    return self._close_protocol_violation(
                        'keepalive_with_segments', packet
                    )
            return True

        return True

    def _decode_packet(self, data, max_size=None, return_size=False):
        """
        Decrypt and decode a packet.

        Args:
            data: Encrypted packet bytes
            max_size: Optional max packet size

        Returns:
            Packet instance or None on error. If return_size is True, returns
            (packet, size) or (None, None) on error.
        """
        if max_size is None:
            max_size = self._max_packet_size
        try:
            if max_size is not None and len(data) > max_size:
                raise ValueError(
                    'Packet size %d exceeds max %d' % (len(data), max_size)
                )
            header = PacketHeader.decode(data)
            body = data[PACKET_HEADER_SIZE:]
            decrypted_body = self._decrypt(
                body,
                seq=header.seq,
                direction=self._direction_inbound(),
            )
            segments = Segment.decode_all(decrypted_body)
            log_control_segments(segments)
            packet = Packet()
            packet.header = header
            packet.segments = segments
            if not self._validate_content_flags(packet):
                if return_size:
                    return (None, None)
                return None
            if return_size:
                return (packet, len(data))
            return packet
        except (ValueError, TypeError) as e:
            def build_fields():
                fields = {
                    'error': str(e),
                    'side': 'alice' if self._is_initiator else 'bob',
                }
                if max_size is not None:
                    fields['max_size'] = max_size
                try:
                    fields['bytes'] = len(data)
                except Exception:
                    pass
                return fields
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.packet_decode_failed',
                'Packet decode failed',
                build_fields,
            )
            if return_size:
                return (None, None)
            return None

    def _process_incoming_packet(self, packet, now=None, packet_size=None):
        """
        Process an incoming packet.

        - Updates send_window with ACK/SACK from peer
        - Passes packet through recv_window for ordering/dedup
        - Delivers in-order segments to channels
        - Processes control messages

        Args:
            packet: Decoded Packet instance
            now: Current time (default: time_provider.now())
            packet_size: Optional encoded packet size from decode

        Returns:
            tuple: (rtt_samples, acked_count, data_acked_count)
        """
        if now is None:
            now = time_provider.now()

        # Process ACK/SACK from peer (updates our send window)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_recv',
            'Packet received',
            lambda: {
                'seq': packet.seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'content_flag': self._content_flag_label(packet.flags),
                'poll_hint': bool(packet.flags & FLAG_POLL_HINT),
                'seg_count': len(packet.segments),
                'bytes': packet_size
                if packet_size is not None else packet.encoded_size(),
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
                'negotiated_window': self.negotiated_window,
                'unacked': self._send_window.unacked_count,
                'side': 'alice' if self._is_initiator else 'bob',
                'state': self._state,
            },
        )
        sample_rtt = bool(packet.flags & FLAG_HAS_SEGMENTS)
        (rtt_samples, acked_count, data_acked_count, unacked_before,
         unacked_after, prev_cum_ack, prev_cum_ack_time, _ack_advanced,
         _ack_progressed) = self._send_window.process_ack_with_progress(
             packet.ack, packet.sack, now=now, sample_rtt=sample_rtt
         )
        def build_ack_fields():
            fields = {
                'ack': packet.ack,
                'sack': packet.sack,
                'acked_count': acked_count,
                'data_acked_count': data_acked_count,
                'rtt_sample_count': len(rtt_samples),
                'rtt_samples_ms': [round(sample, 3) for sample in rtt_samples],
                'unacked_before': unacked_before,
                'unacked_after': unacked_after,
                'prev_cum_ack': prev_cum_ack,
                'side': 'alice' if self._is_initiator else 'bob',
            }
            if prev_cum_ack_time is not None:
                silence = now - prev_cum_ack_time
                if silence < 0:
                    silence = 0.0
                fields['ack_silence'] = round(silence, 6)
            fields.update(self._prefix_fields(
                'send_', self._send_window.debug_state(now=now)
            ))
            fields.update(self._prefix_fields(
                'recv_', self._recv_window.debug_state()
            ))
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.ack_detail',
            'ACK processed detail',
            build_ack_fields,
        )
        if unacked_before != unacked_after or unacked_after > 0:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.ack',
                'ACK processed',
                lambda: {
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'unacked_before': unacked_before,
                    'unacked_after': unacked_after,
                    'acked_count': acked_count,
                    'data_acked_count': data_acked_count,
                    'rtt_sample_count': len(rtt_samples),
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )

        # Pass through recv_window for ordering and deduplication
        # recv_window.receive() returns list of (seq, packet) ready for delivery
        ready_packets, recv_info = self._recv_window.receive(
            packet.seq, packet, return_info=True
        )
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.recv_window',
            'recv_window ready packets',
            lambda: dict(
                {
                    'seq': packet.seq,
                    'ready': len(ready_packets),
                    'side': 'alice' if self._is_initiator else 'bob',
                },
                **self._prefix_fields('recv_', recv_info),
            ),
        )
        if recv_info is not None and recv_info.get('action') in (
                'duplicate',
                'out_of_window',
                'buffer_full',
        ):
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after recv drop',
                now=now,
                include_buffered=True,
                extra_fields={
                    'context': 'recv_drop',
                    'recv_action': recv_info.get('action'),
                    'recv_seq': recv_info.get('seq'),
                    'recv_offset': recv_info.get('offset'),
                },
            )

        # Deliver segments from in-order packets only
        delivered_segments = False
        for seq, ready_packet in ready_packets:
            if ready_packet.flags & FLAG_KEEPALIVE:
                continue
            if not ready_packet.segments:
                continue
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.deliver_segments',
                'Delivering segments',
                lambda: {'seq': seq, 'segments': len(ready_packet.segments)},
            )
            control_segments = []
            data_segments = []
            for segment in ready_packet.segments:
                if segment.is_control:
                    control_segments.append(segment)
                else:
                    data_segments.append(segment)

            for segment in control_segments:
                self._channel_manager.deliver_segment(segment)
                delivered_segments = True

            if control_segments:
                self._process_control_messages()

            for segment in data_segments:
                self._channel_manager.deliver_segment(segment)
                delivered_segments = True

        # Process any remaining control messages
        if delivered_segments:
            self._process_control_messages()

        self._packets_received += 1

        return (rtt_samples, acked_count, data_acked_count)

    def _process_control_messages(self):
        """Process pending control messages from channel 0."""
        ctrl = self._channel_manager.control
        count = 0
        while True:
            try:
                msg = ctrl.recv_message(timeout=self._config.non_blocking_poll_timeout)
                if msg is None:
                    break
                count += 1
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.control_dispatch',
                    'Dispatching control message',
                    lambda: {'t': msg.get('t'), 'c': msg.get('c')},
                )
                self._dispatch_control_message(msg)
            except ChannelError as e:
                # Invalid JSON - log and drop
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.control_invalid',
                    'Invalid control message',
                    lambda: {'error': str(e)},
                )
                break
        if count > 0:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.control_processed',
                'Processed control messages',
                lambda: {'count': count},
            )

    def register_module(self, type_code, handler):
        """
        Register a module to handle control messages of a given type.

        Args:
            type_code: Message type (t field), e.g., 'file', 'sh', 'sock'
            handler: Callable that takes a message dict

        Raises:
            ValueError: If type_code is reserved or already registered
        """
        with self._module_handlers_lock:
            if type_code in self.RESERVED_TYPES:
                raise ValueError('Cannot register reserved type: %s' % type_code)
            if type_code in self._module_handlers:
                raise ValueError('Handler already registered: %s' % type_code)
            self._module_handlers[type_code] = handler
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.module_register',
            'Registered module handler',
            lambda: {'type': type_code},
        )

    def unregister_module(self, type_code):
        """
        Unregister a module handler.

        Args:
            type_code: Message type to unregister

        Returns:
            bool: True if handler was removed, False if not found
        """
        with self._module_handlers_lock:
            if type_code in self._module_handlers:
                del self._module_handlers[type_code]
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.module_unregister',
                    'Unregistered module handler',
                    lambda: {'type': type_code},
                )
                return True
        return False

    def _dispatch_control_message(self, msg):
        """
        Dispatch a control message to the appropriate handler.

        Message format: {"t": "<type>", "c": "<command>", ...}
        - t: Message type (tun, ch, or module type)
        - c: Command within that type

        Dispatch order:
        1. t="tun" -> tunnel handles (negotiation)
        2. t="ch"  -> channel_manager handles (open/close)
        3. t=other -> registered module handler
        4. Unknown -> log and drop

        Args:
            msg: Decoded JSON object
        """
        msg_type = msg.get('t')
        cmd = msg.get('c')

        if not msg_type or not cmd:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.control_invalid',
                'Invalid control message',
                lambda: {'reason': 'missing t or c'},
            )
            return

        if msg_type == 'tun':
            self._handle_tunnel_message(cmd, msg)
        elif msg_type == 'ch':
            self._handle_channel_message(cmd, msg)
        else:
            handler = None
            with self._module_handlers_lock:
                handler = self._module_handlers.get(msg_type)
            if handler is not None:
                try:
                    handler(msg)
                except Exception as e:
                    log_event(
                        self._logger,
                        logging.WARNING,
                        'tunnel.module_error',
                        'Module handler error',
                        lambda: {'type': msg_type, 'error': str(e)},
                    )
            else:
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.control_unknown',
                    'Unknown message type',
                    lambda: {'type': msg_type},
                )

    def _handle_tunnel_message(self, cmd, msg):
        """
        Handle tunnel-level control messages (t="tun").

        Commands: mtu, mtu_ok, mtu_ack, window, window_ok
        Legacy ping/pong control messages are ignored; keepalive uses header flag.
        """
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.command',
            'Tunnel command received',
            lambda: {
                'cmd': cmd,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        if cmd in ('ping', 'pong'):
            return
        if cmd == 'mtu':
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
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.command_unknown',
                'Unknown tunnel command',
                lambda: {
                    'cmd': cmd,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )

    def _handle_channel_message(self, cmd, msg):
        """
        Handle channel-level control messages (t="ch").

        Delegates to channel_manager.
        """
        if cmd == 'half_close' and msg.get('ch') == 0:
            self._close_protocol_violation('half_close_on_control_channel')
            return
        self._channel_manager.handle_control_message(msg)

    def _handle_ping(self, msg):
        """Legacy ping/pong handler (ignored)."""
        return

    def _handle_mtu(self, msg):
        """
        Handle MTU negotiation request (Bob receives from Alice).

        Responds with mtu_ok containing per-direction MTUs.
        Applies downsizes immediately; increases wait for mtu_ack from Alice.
        """
        default_payload = self._payload_mtu_from_packet(self._default_packet_mtu)
        peer_send_payload = msg.get('tx', default_payload)
        peer_recv_payload = msg.get('rx', default_payload)
        if (not isinstance(peer_send_payload, integer_types) or peer_send_payload < 1 or
                not isinstance(peer_recv_payload, integer_types) or peer_recv_payload < 1):
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.mtu_invalid',
                'Invalid MTU request',
                lambda: {
                    'msg': msg,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )
            return
        peer_send_packet_mtu = self._packet_mtu_from_payload(peer_send_payload)
        peer_recv_packet_mtu = self._packet_mtu_from_payload(peer_recv_payload)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_propose',
            'MTU request received (tx_payload=%d rx_payload=%d)' % (
                peer_send_payload, peer_recv_payload
            ),
            lambda: {
                'tx_payload': peer_send_payload,
                'rx_payload': peer_recv_payload,
                'side': 'alice' if self._is_initiator else 'bob',
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
            },
        )

        prev_send = self._send_packet_mtu
        prev_recv = self._recv_packet_mtu

        # Negotiate each direction independently.
        agreed_recv_packet_mtu = min(
            peer_send_packet_mtu,
            self._proposed_recv_packet_mtu or self._default_packet_mtu,
        )
        agreed_send_packet_mtu = min(
            peer_recv_packet_mtu,
            self._proposed_send_packet_mtu or self._default_packet_mtu,
        )

        self._recv_packet_mtu = agreed_recv_packet_mtu
        self._max_packet_size = agreed_recv_packet_mtu

        # Downsize immediately; only defer increases until mtu_ack arrives.
        if agreed_send_packet_mtu <= self._send_packet_mtu:
            self._send_packet_mtu = agreed_send_packet_mtu
            self._pending_send_packet_mtu = None
        else:
            self._pending_send_packet_mtu = agreed_send_packet_mtu

        # Send confirmation (using small packets still)
        self.control.send_message(
            tun_mtu_ok(
                self._payload_mtu_from_packet(agreed_send_packet_mtu),
                self._payload_mtu_from_packet(agreed_recv_packet_mtu),
            )
        )
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ok',
            'MTU negotiate response (recv=%d send_applied=%d send_pending=%s)' % (
                agreed_recv_packet_mtu,
                self._send_packet_mtu,
                self._pending_send_packet_mtu,
            ),
            lambda: {
                'recv_packet_mtu': agreed_recv_packet_mtu,
                'send_applied': self._send_packet_mtu,
                'send_pending': self._pending_send_packet_mtu,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        if prev_send != self._send_packet_mtu or prev_recv != self._recv_packet_mtu:
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.mtu_change',
                'MTU updated',
                lambda: {
                    'prev_send': prev_send,
                    'prev_recv': prev_recv,
                    'send_packet_mtu': self._send_packet_mtu,
                    'recv_packet_mtu': self._recv_packet_mtu,
                    'context': 'mtu_request',
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )

    def _handle_mtu_ok(self, msg):
        """
        Handle MTU negotiation response (Alice receives from Bob).

        Updates negotiated send/recv MTU, then sends mtu_ack.
        """
        default_payload = self._payload_mtu_from_packet(self._default_packet_mtu)
        peer_send_payload = msg.get('tx', default_payload)
        peer_recv_payload = msg.get('rx', default_payload)
        if (not isinstance(peer_send_payload, integer_types) or peer_send_payload < 1 or
                not isinstance(peer_recv_payload, integer_types) or peer_recv_payload < 1):
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.mtu_invalid',
                'Invalid MTU response',
                lambda: {
                    'msg': msg,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )
            return
        peer_send_packet_mtu = self._packet_mtu_from_payload(peer_send_payload)
        peer_recv_packet_mtu = self._packet_mtu_from_payload(peer_recv_payload)

        prev_send = self._send_packet_mtu
        prev_recv = self._recv_packet_mtu

        # Clamp to our transport limits.
        agreed_send_packet_mtu = min(
            peer_recv_packet_mtu,
            self._proposed_send_packet_mtu or self._default_packet_mtu,
        )
        agreed_recv_packet_mtu = min(
            peer_send_packet_mtu,
            self._proposed_recv_packet_mtu or self._default_packet_mtu,
        )

        self._send_packet_mtu = agreed_send_packet_mtu
        self._recv_packet_mtu = agreed_recv_packet_mtu
        self._pending_send_packet_mtu = None
        self._max_packet_size = agreed_recv_packet_mtu
        self._mtu_negotiated = True

        # Send ack so Bob knows he can also start sending larger packets
        self.control.send_message(tun_mtu_ack())
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ok',
            'MTU negotiated',
            lambda: {
                'send_packet_mtu': agreed_send_packet_mtu,
                'recv_packet_mtu': agreed_recv_packet_mtu,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        if prev_send != self._send_packet_mtu or prev_recv != self._recv_packet_mtu:
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.mtu_change',
                'MTU updated',
                lambda: {
                    'prev_send': prev_send,
                    'prev_recv': prev_recv,
                    'send_packet_mtu': self._send_packet_mtu,
                    'recv_packet_mtu': self._recv_packet_mtu,
                    'context': 'mtu_ok',
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )

    def _handle_mtu_ack(self, msg):
        """
        Handle MTU negotiation acknowledgment (Bob receives from Alice).

        Now Bob can safely use the larger MTU for sending.
        """
        prev_send = self._send_packet_mtu
        prev_recv = self._recv_packet_mtu
        if self._pending_send_packet_mtu is not None:
            self._send_packet_mtu = self._pending_send_packet_mtu
            self._pending_send_packet_mtu = None
        self._mtu_negotiated = True
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_ack',
            'MTU ack applied',
            lambda: {
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        if prev_send != self._send_packet_mtu or prev_recv != self._recv_packet_mtu:
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.mtu_change',
                'MTU updated',
                lambda: {
                    'prev_send': prev_send,
                    'prev_recv': prev_recv,
                    'send_packet_mtu': self._send_packet_mtu,
                    'recv_packet_mtu': self._recv_packet_mtu,
                    'context': 'mtu_ack',
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )

    def _handle_window(self, msg):
        """
        Handle window negotiation request (Bob receives from Alice).

        Responds with window_ok containing min(requested, our_max, 64).
        """
        requested = msg.get('size', self._default_window)
        if not isinstance(requested, integer_types) or requested < 1:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.window_invalid',
                'Invalid window request',
                lambda: {
                    'size': requested,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )
            return
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window request received (size=%d max_in_flight=%d)' % (
                requested,
                self._proposed_window,
            ),
            lambda: {
                'size': requested,
                'side': 'alice' if self._is_initiator else 'bob',
                'max_in_flight': self._proposed_window,
            },
        )

        # Negotiate: use minimum of requested, our proposed, and max (64)
        agreed = min(requested, self._proposed_window, self.MAX_WINDOW)
        final = requested >= self._proposed_window
        self._window_negotiated = True

        # Update send window limit
        self._send_window._max_in_flight = agreed

        # Send confirmation
        self.control.send_message(tun_window_ok(agreed, final=final))
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_ok',
            'Window negotiated (requested=%d agreed=%d)' % (requested, agreed),
            lambda: {
                'requested': requested,
                'agreed': agreed,
                'final': final,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )

    def _handle_window_ok(self, msg):
        """
        Handle window negotiation response (Alice receives from Bob).

        Updates negotiated_window and send_window limit.
        """
        reported = msg.get('size', self._default_window)
        final = bool(msg.get('final'))
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.window_ok_recv',
            'Window response received',
            lambda: {
                'size': reported,
                'msg': msg,
                'final': final,
                'negotiated_window': self.negotiated_window,
                'window_negotiated': self._window_negotiated,
                'send_window_max': self._send_window._max_in_flight,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        if not isinstance(reported, integer_types) or reported < 1:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.window_invalid',
                'Invalid window response',
                lambda: {
                    'size': reported,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )
            return
        max_allowed = min(self._proposed_window, self.MAX_WINDOW)
        if reported > max_allowed:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.window_clamp',
                'Window response exceeds local max',
                lambda: {
                    'size': reported,
                    'max_allowed': max_allowed,
                    'side': 'alice' if self._is_initiator else 'bob',
                },
            )
        agreed = min(reported, max_allowed)

        prev_negotiated = self.negotiated_window
        prev_window_negotiated = self._window_negotiated
        prev_send_window = self._send_window._max_in_flight

        self._window_negotiated = True
        if final:
            self._window_final = True

        # Update send window limit
        self._send_window._max_in_flight = agreed
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.window_ok_apply',
            'Window response applied',
            lambda: {
                'agreed': agreed,
                'prev_negotiated_window': prev_negotiated,
                'prev_window_negotiated': prev_window_negotiated,
                'prev_send_window_max': prev_send_window,
                'final': final,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_ok',
            'Window updated (size=%d)' % agreed,
            lambda: {
                'agreed': agreed,
                'final': final,
                'side': 'alice' if self._is_initiator else 'bob',
            },
        )

    def _collect_segments(self, max_payload, return_pending=False,
                          control_only=False, payload_cap=None):
        """
        Collect segments from channels for transmission.

        Args:
            max_payload: Max bytes for segments
            return_pending: If True, return (segments, pending_data)
            control_only: If True, only collect control channel segments
            payload_cap: Optional packet byte cap for this send
        Returns:
            list: List of Segment instances if return_pending is False
            tuple: (segments, pending_data) if return_pending is True
        """
        if payload_cap is not None:
            cap_payload = payload_cap - PACKET_HEADER_SIZE
            if cap_payload < 0:
                cap_payload = 0
            if max_payload > cap_payload:
                max_payload = cap_payload
        return self._channel_manager.collect_segments(
            max_payload,
            return_pending=return_pending,
            control_only=control_only,
        )

    def start_background(self):
        """
        Start the tunnel's message loop in a background thread.

        The loop runs until stop_background() is called or the tunnel closes.
        Subclasses must implement _run_loop() to define their specific loop.
        """
        with self._bg_lock:
            if self._bg_thread is not None:
                if self._bg_thread.is_alive():
                    return  # Already running
                self._bg_thread = None

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
        if timeout is None:
            timeout = self._config.tunnel_bg_stop_timeout
        with self._bg_lock:
            self._bg_stop = True
            if self._bg_thread is None:
                return
            self._bg_thread.join(timeout=timeout)
            if self._bg_thread.is_alive():
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.bg_stop_timeout',
                    'Background thread still running after stop timeout',
                    lambda: {'side': 'alice' if self._is_initiator else 'bob'},
                )
                return
            self._bg_thread = None

    def _bg_run(self):
        """Background thread entry point."""
        try:
            self._run_loop()
        except Exception as e:
            if not self._bg_stop:
                log_event(
                    self._logger,
                    logging.ERROR,
                    'tunnel.bg_error',
                    'Background loop error',
                    lambda: {
                        'error': str(e),
                        'side': 'alice' if self._is_initiator else 'bob',
                    },
                    exc_info=True,
                )

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
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.closed',
            'Tunnel closed',
            lambda: {'side': 'alice' if self._is_initiator else 'bob'},
        )

    def enable_module_loader(self, logger=None):
        """Enable and return the module loader service."""
        if self._module_loader is not None:
            return self._module_loader
        from .module_loader import ModuleLoader
        self._module_loader = ModuleLoader(self, logger=logger or self._logger)
        return self._module_loader
