# -*- coding: ascii -*-
"""
Alice's tunnel implementation (client side).

Alice initiates the connection and polls Bob for data using a
pipelined request/response transport.
"""

from __future__ import absolute_import

import time

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from .tunnel_control_messages import (
    tun_ping,
    tun_mtu,
    tun_window,
    encode as encode_message,
)
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    PACKET_HEADER_SIZE,
)
from ..reliability import RttEstimator


class AliceTunnel(BaseTunnel):
    """
    Client-side tunnel.

    Alice initiates the handshake and drives the tunnel using tick().
    She uses RTT-based retransmission timing and supports pipelining.
    """

    def __init__(self, transport, config, crypto=None, logger=None):
        """
        Initialize Alice's tunnel.

        Args:
            transport: Transport instance
            config: Config instance with tunnel settings
            crypto: Cipher instance (default: Plain)
            logger: Optional logger instance
        """
        super(AliceTunnel, self).__init__(
            config=config,
            crypto=crypto,
            is_initiator=True,
            logger=logger,
        )
        self._transport = transport
        self._keepalive_interval = config.tunnel_keepalive_interval

        # Set proposed MTU from transport (for negotiation, symmetric)
        max_packet = min(transport.send_mtu, transport.recv_mtu)
        self._proposed_mtu = max(1, max_packet - PACKET_HEADER_SIZE)

        # RTT estimation (Alice only)
        self._rtt = RttEstimator()

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0

        # Timeout detection: packets sent without any response
        self._packets_since_response = 0
        self._max_packets_without_response = config.tunnel_timeout_packets

        # Adaptive polling: poll immediately when Bob sends real data
        self._got_data = False

    @property
    def rtt_estimator(self):
        """RTT estimator instance."""
        return self._rtt

    def connect(self, timeout=None):
        """
        Connect to Bob with handshake.

        Uses serial send/recv during handshake for simplicity.

        Args:
            timeout: Max seconds to wait for handshake (default: from config)

        Raises:
            TunnelError: on handshake failure or timeout
        """
        if timeout is None:
            timeout = self._config.tunnel_connect_timeout
        if self._state != TunnelState.DISCONNECTED:
            raise TunnelError('Already connected or connecting')

        self._set_state(TunnelState.CONNECTING)
        self._local_isn = self._generate_isn()
        self._send_window._next_seq = self._local_isn

        start_time = time.time()
        attempt = 0

        while time.time() - start_time < timeout:
            attempt += 1
            self._logger.debug('Handshake attempt %d', attempt)

            # Build SYN packet
            syn_packet = Packet(
                seq=self._local_isn,
                ack=0,
                sack=0,
                flags=FLAG_SYN,
            )
            syn_data = self._encode_packet(syn_packet)

            try:
                # Send SYN
                self._transport.send(syn_data)

                # Wait for SYN+ACK
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    break
                corr_id, response_data = self._transport.recv(
                    timeout=min(self._rtt.rto_sec, remaining)
                )

                if response_data is None:
                    self._rtt.backoff()
                    continue

                response = self._decode_packet(response_data)
                if response is None:
                    self._rtt.backoff()
                    continue

                # Check for SYN+ACK
                if response.flags == (FLAG_SYN | FLAG_ACK):
                    if response.ack == (self._local_isn + 1) & 0xFFFF:
                        # Valid SYN+ACK
                        self._remote_isn = response.seq
                        self._recv_window.set_initial_seq(
                            (self._remote_isn + 1) & 0xFFFF
                        )
                        self._send_window._next_seq = (
                            self._local_isn + 1
                        ) & 0xFFFF

                        # Send ACK to complete handshake
                        self._complete_handshake(timeout - (time.time() - start_time))
                        return

                self._rtt.backoff()

            except Exception as e:
                self._logger.warning('Handshake error: %s', e)
                self._rtt.backoff()

            # Wait before retry
            time.sleep(min(self._rtt.rto_sec, timeout / 10))

        self._set_state(TunnelState.DISCONNECTED)
        raise TunnelError('Handshake timeout')

    def _complete_handshake(self, remaining_timeout):
        """Send final ACK and transition to CONNECTED."""
        # Use send_window's next seq for ACK so it advances properly
        ack_seq = self._send_window._next_seq
        self._send_window._next_seq = (ack_seq + 1) & 0xFFFF

        ack_packet = Packet(
            seq=ack_seq,
            ack=(self._remote_isn + 1) & 0xFFFF,
            sack=0,
            flags=FLAG_ACK,
        )
        ack_data = self._encode_packet(ack_packet)

        try:
            # Send ACK
            self._transport.send(ack_data)
            self._set_state(TunnelState.CONNECTED)
            self._last_recv_time = time.time()
            self._packets_since_response = 0

            # Wait for response (may contain data or pong)
            corr_id, response_data = self._transport.recv(
                timeout=min(self._rtt.rto_sec, remaining_timeout)
            )

            if response_data:
                response = self._decode_packet(response_data)
                if response:
                    self._process_incoming_packet(response)

            self._logger.info('Connected (local_isn=%d, remote_isn=%d)',
                              self._local_isn, self._remote_isn)

            # Initiate MTU and window negotiation
            self._send_negotiation()

        except Exception as e:
            self._logger.warning('Failed to send ACK: %s', e)
            # Still mark as connected - Bob will accept data as implicit ACK
            self._set_state(TunnelState.CONNECTED)
            # Still try to negotiate
            self._send_negotiation()

    def _send_negotiation(self):
        """Queue MTU and window negotiation messages."""
        # Queue MTU request
        self.control.send_message(tun_mtu(self._proposed_mtu))
        self._logger.debug('Requesting MTU: %d', self._proposed_mtu)

        # Queue window request
        self.control.send_message(tun_window(self._proposed_max_in_flight))
        self._logger.debug('Requesting window: %d', self._proposed_max_in_flight)

    def tick(self):
        """
        Perform one iteration of the event loop.

        - Receives all available responses (non-blocking)
        - Checks for retransmits
        - Sends new packets up to limit

        Returns:
            bool: True if still running, False if closed
        """
        if self._state != TunnelState.CONNECTED:
            return False

        now = time.time()

        # 1. Receive all available responses
        received_any = False
        self._got_data = False  # Reset - will be set if we get non-pong data
        while True:
            corr_id, data = self._transport.recv(timeout=0)
            if corr_id is None:
                break
            self._handle_response(data, now)
            received_any = True

        if received_any:
            self._packets_since_response = 0

        # Check connection timeout
        if self._packets_since_response >= self._max_packets_without_response:
            self._set_state(TunnelState.CLOSED)
            self._logger.error('Connection timeout after %d packets without response',
                               self._max_packets_without_response)
            return False

        # 2. Check for retransmits
        retransmits = self._send_window.get_retransmits(
            self._rtt.rto_sec, now=now
        )
        for seq, segments in retransmits:
            if not self._can_send_retransmit():
                break
            self._send_retransmit(seq, segments, now)

        # 3. Send new packets if we can
        send_count = 0
        while self._can_send_new():
            segments = self._collect_segments(self._negotiated_mtu)
            if not segments:
                # No data to send - decide whether to poll
                # Poll immediately if:
                # - Bob sent real data (not just pong) - need more data
                # - We received any response - need to send updated ACKs
                # Otherwise respect keepalive interval
                should_poll = received_any or self._got_data or (
                    now - self._last_send_time >= self._keepalive_interval
                )
                if should_poll:
                    segments = self._collect_segments(
                        self._negotiated_mtu,
                        keepalive_data=encode_message(tun_ping())
                    )
                    # Don't reset _got_data here - let the loop fill the pipeline
                if not segments:
                    break
            self._send_new_packet(segments, now)

        return True

    def _can_send_new(self):
        """Check if we can send a new packet."""
        return (self._transport.pending_count() < self._transport.max_pending and
                self._send_window.can_send)

    def _can_send_retransmit(self):
        """Check if we can send a retransmit packet."""
        return self._transport.pending_count() < self._transport.max_pending

    def _send_new_packet(self, segments, now):
        """Send a new packet with given segments."""
        packet, seq = self._build_packet(segments=segments)
        packet_data = self._encode_packet(packet)

        self._send_window.send(segments, now=now)
        self._transport.send(packet_data)

        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        self._packets_since_response += 1

    def _send_retransmit(self, seq, segments, now):
        """Retransmit a packet."""
        packet = self._rebuild_packet(seq, segments)
        packet_data = self._encode_packet(packet)

        self._send_window.mark_retransmit(seq, now=now)
        self._transport.send(packet_data)

        self._rtt.backoff()
        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        self._packets_since_response += 1
        self._logger.debug('Retransmitting seq=%d', seq)

    def _handle_response(self, data, now):
        """Handle a transport response."""
        packet = self._decode_packet(data)
        if packet is None:
            return

        self._bytes_received += len(data)
        self._last_recv_time = now

        # Check if packet contains real data (not just pong)
        # Real data = any data segment, or control messages other than pong
        for seg in packet.segments:
            if not seg.is_control:
                # Data segment - definitely real data
                self._got_data = True
            else:
                # Control segment - check if it's not just pong
                # Control data is newline-delimited JSON
                if b'"c":"pong"' not in seg.data and b'"c": "pong"' not in seg.data:
                    self._got_data = True

        rtt_samples = self._process_incoming_packet(packet, now=now)

        for sample in rtt_samples:
            self._rtt.add_sample(sample)

    def run(self, duration=None):
        """
        Run the tunnel for a duration or until closed.

        Args:
            duration: Max seconds to run (None = until closed)
        """
        start = time.time()
        while self._state == TunnelState.CONNECTED:
            self.tick()

            if duration and (time.time() - start) >= duration:
                break

            # Brief sleep to avoid busy loop
            time.sleep(0.001)

    def close(self):
        """Close the tunnel and transport."""
        super(AliceTunnel, self).close()
        try:
            self._transport.close()
        except Exception:
            pass
