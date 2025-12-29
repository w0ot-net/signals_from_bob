# -*- coding: ascii -*-
"""
Bob's tunnel implementation (server side).

Bob waits for Alice's requests and responds using a request/response
transport server.
"""

from __future__ import absolute_import

import time

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from .tunnel_control_messages import tun_pong, encode as encode_message
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
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

        # Set proposed MTU from transport (for negotiation, asymmetric)
        send_payload = max(1, transport.send_mtu - PACKET_HEADER_SIZE)
        recv_payload = max(1, transport.recv_mtu - PACKET_HEADER_SIZE)
        self._proposed_send_mtu = send_payload
        self._proposed_recv_mtu = recv_payload

        # Use transport's actual limits before negotiation completes
        self._send_mtu = send_payload
        self._recv_mtu = recv_payload
        self._max_packet_size = recv_payload + PACKET_HEADER_SIZE
        self._logger.debug('BobTunnel init: transport.recv_mtu=%d recv_payload=%d max_packet_size=%d',
                          transport.recv_mtu, recv_payload, self._max_packet_size)

        # Timing
        self._last_request_time = 0

        # Handshake state
        self._handshake_complete = False

    def serve_forever(self):
        """
        Serve requests until closed or idle timeout.
        """
        self._logger.info('Waiting for connections...')

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
                self._logger.warning('Error in serve loop: %s', e)

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
                    self._logger.warning('Serve loop error: %s', e)

    def handle_request(self, data, responder):
        """
        Handle a single request from Alice.

        Args:
            data: Encrypted packet bytes from Alice
            responder: Callable to send response
        """
        now = time.time()
        self._last_request_time = now

        # Decode incoming packet
        packet = self._decode_packet(data)
        if packet is None:
            self._logger.warning('Failed to decode request')
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
            self._logger.warning('Request in unexpected state: %s', self._state)

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
            responder(response_data)

            self._packets_sent += 1
            self._bytes_sent += len(response_data)

            self._logger.debug('Sent SYN+ACK (local_isn=%d, remote_isn=%d)',
                               self._local_isn, self._remote_isn)

        elif packet.flags & FLAG_ACK:
            # ACK from Alice - handshake complete
            if packet.ack == (self._local_isn + 1) & 0xFFFF:
                self._send_window._next_seq = (self._local_isn + 1) & 0xFFFF
                self._set_state(TunnelState.CONNECTED)
                self._handshake_complete = True
                self._logger.info('Connected (local_isn=%d, remote_isn=%d)',
                                  self._local_isn, self._remote_isn)

                # Process any data in the ACK packet
                self._process_incoming_packet(packet, now=now)

                # Send response
                self._send_response(responder, now)

        elif self._state == TunnelState.CONNECTING:
            # Data packet while connecting - treat as implicit ACK
            self._send_window._next_seq = (self._local_isn + 1) & 0xFFFF
            self._set_state(TunnelState.CONNECTED)
            self._handshake_complete = True
            self._logger.info('Connected via implicit ACK')

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
        # Opportunistic retransmit: if we have unacked packets, resend oldest
        # Rebuild with fresh ack/sack to ensure current ACK state is sent
        oldest = self._send_window.get_oldest_unacked()
        if oldest is not None:
            seq, segments = oldest
            packet = self._rebuild_packet(seq, segments)
            response_data = self._encode_packet(packet)
            self._send_window.mark_retransmit(seq, now=now)
            self._logger.debug('Retransmitting seq=%d', seq)

            self._packets_sent += 1
            self._bytes_sent += len(response_data)
            responder(response_data)
            return

        # No retransmits needed - check window before sending new data
        if not self._send_window.can_send:
            # Window full but no unacked? Shouldn't happen - log and send pong
            # to maintain request/response contract
            self._logger.error('Send window full but no unacked packets')
            max_payload = self._send_mtu
            segments = self._collect_segments(
                max_payload,
                keepalive_data=encode_message(tun_pong())
            )
            packet, _ = self._build_packet(segments=segments)
            response_data = self._encode_packet(packet)
            self._packets_sent += 1
            self._bytes_sent += len(response_data)
            responder(response_data)
            return

        # Collect new segments - use send MTU
        max_payload = self._send_mtu
        segments = self._collect_segments(max_payload)

        # If no data, send pong
        if not segments:
            segments = self._collect_segments(
                max_payload,
                keepalive_data=encode_message(tun_pong())
            )

        # Build packet
        packet, seq = self._build_packet(segments=segments)
        response_data = self._encode_packet(packet)

        # Record send (store segments for retransmit with fresh ack/sack)
        self._send_window.send(segments, now=now)
        self._packets_sent += 1
        self._bytes_sent += len(response_data)

        # Send response
        responder(response_data)

    def _check_idle_timeout(self):
        """Check if connection has timed out (including stalled handshake)."""
        # Check both CONNECTED and CONNECTING states
        if self._state not in (TunnelState.CONNECTED, TunnelState.CONNECTING):
            return False

        if self._last_request_time == 0:
            return False

        elapsed = time.time() - self._last_request_time
        if elapsed > self._idle_timeout:
            if self._state == TunnelState.CONNECTING:
                self._logger.warning('Handshake timeout (%.1fs)', elapsed)
            else:
                self._logger.warning('Idle timeout (%.1fs)', elapsed)
            self._set_state(TunnelState.CLOSED)
            return True

        return False

    def allow_message_type(self, msg_type):
        """
        Allow a message type from Alice.

        Called after module is loaded to enable module messages.

        Args:
            msg_type: Message type to allow (e.g., 'file', 'mod')
        """
        self._allowed_message_types.add(msg_type)
        self._logger.debug('Allowed message type: %s', msg_type)

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
            self._logger.warning('Rejected message type from Alice: %s', msg_type)
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
