# -*- coding: ascii -*-
"""
Bob's tunnel implementation (server side).

Bob waits for Alice's requests and responds using a request/response
transport server.
"""

from __future__ import absolute_import

import logging
import time

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from ..logging_util import log_event
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    PACKET_HEADER_SIZE,
)


class BobTunnel(BaseTunnel):
    """
    Server-side tunnel.

    Bob waits for Alice's polls using transport.recv() and responds
    via the responder callback. He uses opportunistic retransmission.

    Security: By default, Bob only accepts 'tun' and 'ch' messages from Alice.
    Other message types must be explicitly allowed via allow_message_type().
    """

    def __init__(self, transport, config, crypto=None, logger=None):
        """
        Initialize Bob's tunnel.

        Args:
            transport: Server instance
            config: Config instance with tunnel settings
            crypto: Cipher instance (default: Plain)
            logger: Optional logger instance
        """
        super(BobTunnel, self).__init__(
            config=config,
            crypto=crypto,
            is_initiator=False,
            logger=logger,
        )
        self._transport = transport
        self._idle_timeout = config.tunnel_idle_timeout

        # Security: only accept these message types from Alice by default
        self._allowed_message_types = {'tun', 'ch'}

        _send_payload, recv_payload = self._init_transport_limits(transport)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.init',
            'Tunnel init',
            lambda: {
                'transport_recv_mtu': transport.recv_mtu,
                'recv_payload': recv_payload,
                'max_packet_size': self._max_packet_size,
                'side': 'bob',
            },
        )

        # Timing
        self._last_request_time = None
        self._poll_interval_ewma = None

        # Handshake state
        self._handshake_complete = False

    def serve_forever(self):
        """
        Serve requests until closed or idle timeout.
        """
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.wait',
            'Waiting for connections',
            lambda: {'side': 'bob'},
        )

        while self._state != TunnelState.CLOSED:
            try:
                result = self._transport.recv(timeout=self._config.tunnel_bob_poll_interval)
                if result is None or result[0] is None:
                    # Timeout - check idle
                    if self._check_idle_timeout():
                        break
                    continue

                data, responder = result
                self.handle_request(data, responder)

            except Exception as e:
                # Suppress socket errors during shutdown
                if self._state == TunnelState.CLOSED:
                    break
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.serve_error',
                    'Serve loop error',
                    lambda: {
                        'error': str(e),
                        'loop': 'serve_forever',
                        'side': 'bob',
                    },
                    exc_info=True,
                )

    def _run_loop(self):
        """Background thread loop - processes requests until stopped."""
        while not self._bg_stop and self._state != TunnelState.CLOSED:
            try:
                result = self._transport.recv(timeout=self._config.tunnel_bob_poll_interval_bg)
                if result is None or result[0] is None:
                    continue
                data, responder = result
                self.handle_request(data, responder)
            except Exception as e:
                if not self._bg_stop:
                    log_event(
                        self._logger,
                        logging.WARNING,
                        'tunnel.serve_error',
                        'Serve loop error',
                        lambda: {
                            'error': str(e),
                            'loop': 'background',
                            'side': 'bob',
                        },
                        exc_info=True,
                    )

    def handle_request(self, data, responder):
        """
        Handle a single request from Alice.

        Args:
            data: Encrypted packet bytes from Alice
            responder: Callable to send response
        """
        now = time.time()
        self._update_poll_ewma(now)

        # Decode incoming packet
        packet = self._decode_packet(data)
        if packet is None:
            return

        self._bytes_received += len(data)
        self._packets_received += 1

        # Handle based on state
        if self._state == TunnelState.DISCONNECTED:
            self._handle_handshake(packet, responder, now)
        elif self._state == TunnelState.CONNECTING:
            self._handle_handshake(packet, responder, now)
        elif self._state == TunnelState.CONNECTED:
            self._handle_data(packet, responder, now)
        else:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.request_state_unexpected',
                'Request in unexpected state',
                lambda: {'state': self._state, 'side': 'bob'},
            )

    def _handle_handshake(self, packet, responder, now):
        """Handle handshake packets."""
        if packet.flags & FLAG_SYN:
            # SYN from Alice
            self._remote_isn = packet.seq
            self._local_isn = self._generate_isn()
            self._send_window._next_seq = self._local_isn

            # Initialize recv window to expect Alice's next seq
            self._recv_window.set_initial_seq((self._remote_isn + 1) & 0xFFFF)

            self._set_state(TunnelState.CONNECTING)

            # Send SYN+ACK
            syn_ack = Packet(
                seq=self._local_isn,
                ack=(self._remote_isn + 1) & 0xFFFF,
                sack=0,
                flags=FLAG_SYN | FLAG_ACK,
            )
            response_data = self._encode_packet(syn_ack)
            self._respond(responder, response_data, 'handshake_synack', syn_ack)

            self._packets_sent += 1
            self._bytes_sent += len(response_data)

            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.handshake_synack_sent',
                'Sent SYN+ACK',
                lambda: {
                    'local_isn': self._local_isn,
                    'remote_isn': self._remote_isn,
                    'side': 'bob',
                },
            )

        elif packet.flags & FLAG_ACK:
            # ACK from Alice - handshake complete
            if packet.ack == (self._local_isn + 1) & 0xFFFF:
                self._send_window._next_seq = (self._local_isn + 1) & 0xFFFF
                self._set_state(TunnelState.CONNECTED)
                self._handshake_complete = True
                log_event(
                    self._logger,
                    logging.INFO,
                    'tunnel.connected',
                    'Connected',
                    lambda: {
                        'local_isn': self._local_isn,
                        'remote_isn': self._remote_isn,
                        'mode': 'syn_ack',
                        'side': 'bob',
                    },
                )

                # Process any data in the ACK packet
                self._process_incoming_packet(packet, now=now)

                # Send response
                self._send_response(responder, now)

        elif self._state == TunnelState.CONNECTING:
            # Data packet while connecting - treat as implicit ACK
            self._send_window._next_seq = (self._local_isn + 1) & 0xFFFF
            self._set_state(TunnelState.CONNECTED)
            self._handshake_complete = True
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.connected',
                'Connected',
                lambda: {
                    'local_isn': self._local_isn,
                    'remote_isn': self._remote_isn,
                    'mode': 'implicit_ack',
                    'side': 'bob',
                },
            )

            self._process_incoming_packet(packet, now=now)
            self._send_response(responder, now)

    def _handle_data(self, packet, responder, now):
        """Handle data packets."""
        # Process incoming
        self._process_incoming_packet(packet, now=now)

        # Send response
        self._send_response(responder, now)

    def _send_response(self, responder, now):
        """Build and send response packet."""
        response_payload_cap = None
        if hasattr(responder, 'payload_cap'):
            response_payload_cap = responder.payload_cap

        # Opportunistic retransmit: if we have unacked packets, resend oldest
        # Rebuild with fresh ack/sack to ensure current ACK state is sent
        oldest = self._send_window.get_oldest_unacked_info()
        if oldest is not None:
            seq, segments, flags, send_time, retransmit_count = oldest
            cooldown = self._retransmit_cooldown()
            age = None
            if send_time is not None:
                age = now - send_time
            since_cum_ack = None
            if self._last_cum_ack_time is not None:
                since_cum_ack = now - self._last_cum_ack_time
            skip_reason = None
            if age is not None and age < cooldown:
                skip_reason = 'cooldown'
            elif since_cum_ack is not None and since_cum_ack < cooldown:
                skip_reason = 'ack_progress'
            if skip_reason is not None:
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.retransmit_skip',
                    'Retransmit skipped',
                    lambda: {
                        'seq': seq,
                        'reason': skip_reason,
                        'age': round(age, 6) if age is not None else None,
                        'cooldown': cooldown,
                        'since_cum_ack': round(since_cum_ack, 6)
                        if since_cum_ack is not None else None,
                        'last_cum_ack': self._last_cum_ack,
                        'retransmit_count': retransmit_count,
                        'side': 'bob',
                    },
                )
            else:
                packet = self._rebuild_packet(seq, segments, flags=flags)
                response_data = self._encode_packet(packet)
                if (response_payload_cap is not None and
                        len(response_data) > response_payload_cap):
                    log_event(
                        self._logger,
                        logging.DEBUG,
                        'tunnel.retransmit_skip',
                        'Retransmit exceeds per-request cap',
                        lambda: {
                            'seq': seq,
                            'reason': 'cap',
                            'bytes': len(response_data),
                            'cap': response_payload_cap,
                            'side': 'bob',
                        },
                    )
                    log_event(
                        self._logger,
                        logging.ERROR,
                        'tunnel.retransmit_cap_fatal',
                        'Retransmit exceeds per-request cap; closing',
                        lambda: {
                            'seq': seq,
                            'bytes': len(response_data),
                            'cap': response_payload_cap,
                            'side': 'bob',
                        },
                    )
                    self.close()
                    return
                self._send_window.mark_retransmit(seq, now=now)
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.retransmit',
                    'Retransmitting packet',
                    lambda: {'seq': seq, 'seg_count': len(segments), 'side': 'bob'},
                )
                self._log_response_cap(responder, response_data)

                self._packets_sent += 1
                self._bytes_sent += len(response_data)
                self._respond(responder, response_data, 'retransmit', packet)
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.packet_send',
                    'Packet sent',
                    lambda: {
                        'seq': packet.seq,
                        'ack': packet.ack,
                        'sack': packet.sack,
                        'flags': packet.flags,
                        'seg_count': len(packet.segments),
                        'bytes': len(response_data),
                        'side': 'bob',
                    },
                )
                return

        # No retransmits needed - check window before sending new data
        if not self._send_window.can_send:
            # Window full but no unacked? Shouldn't happen - log and send ACK-only
            # to maintain request/response contract
            unacked = self._send_window.unacked_count
            if unacked == 0:
                log_event(
                    self._logger,
                    logging.ERROR,
                    'tunnel.send_window_inconsistent',
                    'Send window full but no unacked packets',
                    lambda: {
                        'unacked': unacked,
                        'max_in_flight': self._send_window._max_in_flight,
                        'side': 'bob',
                    },
                )
            else:
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.send_window_full',
                    'Send window full',
                    lambda: {
                        'unacked': unacked,
                        'max_in_flight': self._send_window._max_in_flight,
                        'side': 'bob',
                    },
                )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window full',
                lambda: {
                    'unacked': self._send_window.unacked_count,
                    'max_in_flight': self._send_window._max_in_flight,
                    'side': 'bob',
                },
            )
            packet, _ = self._build_packet(segments=[])
            response_data = self._encode_packet(packet)
            self._packets_sent += 1
            self._bytes_sent += len(response_data)
            self._respond(responder, response_data, 'window_full', packet)
            return
        exceeded, distance_info = self._send_window_distance_exceeded()
        if exceeded:
            distance, max_in_flight, last_cum_ack, next_seq = distance_info
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_window_distance',
                'Send window distance exceeded',
                lambda: {
                    'distance': distance,
                    'max_in_flight': max_in_flight,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'bob',
                },
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window distance exceeded',
                lambda: {
                    'distance': distance,
                    'max_in_flight': max_in_flight,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'bob',
                    'reason': 'window_distance',
                },
            )
            packet, _ = self._build_packet(segments=[])
            response_data = self._encode_packet(packet)
            self._packets_sent += 1
            self._bytes_sent += len(response_data)
            self._respond(responder, response_data, 'window_distance', packet)
            return

        # Collect new segments - use send MTU
        max_payload = self._send_mtu
        if response_payload_cap is not None:
            cap_payload = response_payload_cap - PACKET_HEADER_SIZE
            if cap_payload < 0:
                cap_payload = 0
            if cap_payload < max_payload:
                max_payload = cap_payload
        segments, pending_data = self._collect_segments(
            max_payload,
            return_pending=True,
        )

        if not segments:
            if pending_data:
                packet, _ = self._build_packet(segments=[])
                response_data = self._encode_packet(packet)
                self._packets_sent += 1
                self._bytes_sent += len(response_data)
                self._respond(responder, response_data, 'pending_no_segments', packet)
                return
            packet, seq = self._build_packet(
                flags=FLAG_KEEPALIVE,
                segments=[],
            )
            response_data = self._encode_packet(packet)
            self._send_window.send([], flags=FLAG_KEEPALIVE, now=now)
            self._packets_sent += 1
            self._bytes_sent += len(response_data)
            self._log_response_cap(responder, response_data)
            self._respond(responder, response_data, 'keepalive', packet)
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.packet_send',
                'Packet sent',
                lambda: {
                    'seq': packet.seq,
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'flags': packet.flags,
                    'seg_count': len(packet.segments),
                    'bytes': len(response_data),
                    'side': 'bob',
                },
            )
            return

        # Build packet
        packet, seq = self._build_packet(segments=segments)
        response_data = self._encode_packet(packet)

        # Record send (store segments for retransmit with fresh ack/sack)
        self._send_window.send(segments, flags=packet.flags, now=now)
        self._packets_sent += 1
        self._bytes_sent += len(response_data)

        # Send response
        self._log_response_cap(responder, response_data)
        self._respond(responder, response_data, 'segments', packet)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            lambda: {
                'seq': packet.seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(packet.segments),
                'bytes': len(response_data),
                'side': 'bob',
            },
        )

    def _log_response_cap(self, responder, response_data):
        response_payload_cap = None
        if hasattr(responder, 'payload_cap'):
            response_payload_cap = responder.payload_cap
        qname_wire_len = getattr(responder, 'qname_wire_len', None)
        max_packet_size = getattr(responder, 'max_packet_size', None)
        if response_payload_cap is None or qname_wire_len is None:
            return
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.response_cap',
            'DNS response cap detail',
            lambda: {
                'payload_cap': response_payload_cap,
                'qname_wire_len': qname_wire_len,
                'max_packet_size': max_packet_size,
                'response_bytes': len(response_data),
                'side': 'bob',
            },
        )

    def _respond(self, responder, response_data, context, packet=None):
        try:
            responder(response_data)
            return
        except Exception as exc:
            def build_fields():
                fields = {
                    'context': context,
                    'error': str(exc),
                    'side': 'bob',
                }
                try:
                    fields['bytes'] = len(response_data)
                except Exception:
                    pass
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
                'tunnel.responder_error',
                'Responder send failed',
                build_fields,
                exc_info=True,
            )
            raise

    def _check_idle_timeout(self):
        """Check if connection has timed out (including stalled handshake)."""
        # Check both CONNECTED and CONNECTING states
        if self._state not in (TunnelState.CONNECTED, TunnelState.CONNECTING):
            return False

        if self._last_request_time is None:
            return False

        elapsed = time.time() - self._last_request_time
        if elapsed > self._idle_timeout:
            if self._state == TunnelState.CONNECTING:
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.handshake_timeout',
                    'Handshake timeout',
                    lambda: {'elapsed': round(elapsed, 1), 'side': 'bob'},
                )
            else:
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.idle_timeout',
                    'Idle timeout',
                    lambda: {'elapsed': round(elapsed, 1), 'side': 'bob'},
                )
            self._set_state(TunnelState.CLOSED)
            return True

        return False

    def _update_poll_ewma(self, now):
        if self._last_request_time is None:
            self._last_request_time = now
            return
        interval = now - self._last_request_time
        if interval < 0:
            interval = 0.0
        self._last_request_time = now
        alpha = self._config.tunnel_bob_poll_ewma_alpha
        if self._poll_interval_ewma is None:
            self._poll_interval_ewma = interval
        else:
            self._poll_interval_ewma = (
                (alpha * interval) + ((1.0 - alpha) * self._poll_interval_ewma)
            )

    def _retransmit_cooldown(self):
        cooldown = self._config.tunnel_bob_retransmit_min_interval
        factor = self._config.tunnel_bob_retransmit_poll_factor
        if self._poll_interval_ewma is not None and factor > 0:
            cooldown = max(cooldown, self._poll_interval_ewma * factor)
        max_interval = self._config.tunnel_bob_retransmit_max_interval
        if max_interval is not None and max_interval > 0:
            cooldown = min(cooldown, max_interval)
        return cooldown

    def allow_message_type(self, msg_type):
        """
        Allow a message type from Alice.

        Called after module is loaded to enable module messages.

        Args:
            msg_type: Message type to allow (e.g., 'file', 'mod')
        """
        self._allowed_message_types.add(msg_type)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.message_type_allowed',
            'Allowed message type',
            lambda: {'msg_type': msg_type, 'side': 'bob'},
        )

    def enable_module_loader(self, logger=None):
        """Enable module loader and allow 'mod' messages."""
        loader = super(BobTunnel, self).enable_module_loader(logger=logger)
        self.allow_message_type('mod')
        return loader

    def _dispatch_control_message(self, msg):
        """
        Dispatch a control message with security filtering.

        Bob only accepts message types in _allowed_message_types.
        By default this is just 'tun' and 'ch'.
        """
        msg_type = msg.get('t')
        if msg_type not in self._allowed_message_types:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.message_type_rejected',
                'Rejected message type from Alice',
                lambda: {'msg_type': msg_type, 'side': 'bob'},
            )
            return

        # Delegate to parent for actual dispatch
        super(BobTunnel, self)._dispatch_control_message(msg)

    def close(self):
        """Close the tunnel and transport."""
        super(BobTunnel, self).close()
        try:
            self._transport.close()
        except Exception:
            pass
