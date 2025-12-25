# -*- coding: ascii -*-
"""
Alice's tunnel implementation (client side).

Alice initiates the connection and polls Bob for data using a
request/response transport.
"""

from __future__ import absolute_import

import time

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from ..tunnel_control_messages import tun_ping, encode as encode_message
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

    Alice initiates the handshake and polls Bob using transport.exchange().
    She uses RTT-based retransmission timing.
    """

    def __init__(self, transport, crypto=None, keepalive_interval=5.0,
                 max_in_flight=16, logger=None):
        """
        Initialize Alice's tunnel.

        Args:
            transport: RequestResponseTransport instance
            crypto: Cipher instance (default: Plain)
            keepalive_interval: Seconds between keepalives
            max_in_flight: Max unacked packets
            logger: Optional logger instance
        """
        super(AliceTunnel, self).__init__(
            crypto=crypto,
            is_initiator=True,
            max_in_flight=max_in_flight,
            logger=logger,
        )
        self._transport = transport
        self._keepalive_interval = keepalive_interval

        # RTT estimation (Alice only)
        self._rtt = RttEstimator()

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0
        self._consecutive_failures = 0
        self._max_failures = 10

    @property
    def rtt_estimator(self):
        """RTT estimator instance."""
        return self._rtt

    def connect(self, timeout=10.0):
        """
        Connect to Bob with handshake.

        Args:
            timeout: Max seconds to wait for handshake

        Raises:
            TunnelError: on handshake failure or timeout
        """
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
                # Send SYN, expect SYN+ACK
                response_data = self._transport.exchange(syn_data)
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
                        self._complete_handshake()
                        return

                self._rtt.backoff()

            except Exception as e:
                self._logger.warning('Handshake error: %s', e)
                self._rtt.backoff()

            # Wait before retry
            time.sleep(min(self._rtt.rto_sec, timeout / 10))

        self._set_state(TunnelState.DISCONNECTED)
        raise TunnelError('Handshake timeout')

    def _complete_handshake(self):
        """Send final ACK and transition to CONNECTED."""
        ack_packet = Packet(
            seq=(self._local_isn + 1) & 0xFFFF,
            ack=(self._remote_isn + 1) & 0xFFFF,
            sack=0,
            flags=FLAG_ACK,
        )
        ack_data = self._encode_packet(ack_packet)

        try:
            # Send ACK - response may contain data
            response_data = self._transport.exchange(ack_data)
            self._set_state(TunnelState.CONNECTED)
            self._last_recv_time = time.time()
            self._consecutive_failures = 0

            # Process any data in response
            if response_data:
                response = self._decode_packet(response_data)
                if response:
                    self._process_incoming_packet(response)

            self._logger.info('Connected (local_isn=%d, remote_isn=%d)',
                              self._local_isn, self._remote_isn)

        except Exception as e:
            self._logger.warning('Failed to send ACK: %s', e)
            # Still mark as connected - Bob will accept data as implicit ACK
            self._set_state(TunnelState.CONNECTED)

    def poll(self):
        """
        Perform one request/response cycle.

        - Checks for retransmits
        - Collects data from channels
        - Sends packet to Bob
        - Processes response

        Returns:
            bool: True if exchange succeeded, False on error
        """
        if self._state != TunnelState.CONNECTED:
            return False

        now = time.time()

        # Check for retransmits
        retransmits = self._send_window.get_retransmits(
            self._rtt.rto_sec, now=now
        )

        packet_data = None
        segments = None

        if retransmits:
            # Retransmit oldest - rebuild with fresh ack/sack
            seq, segments = retransmits[0]
            packet = self._rebuild_packet(seq, segments)
            packet_data = self._encode_packet(packet)
            self._send_window.mark_retransmit(seq, now=now)
            self._rtt.backoff()
            self._logger.debug('Retransmitting seq=%d', seq)
        elif not self._send_window.can_send:
            # Window full - retransmit oldest to carry fresh ACK/SACK
            oldest = self._send_window.get_oldest_unacked()
            if oldest is not None:
                seq, segments = oldest
                packet = self._rebuild_packet(seq, segments)
                packet_data = self._encode_packet(packet)
                self._send_window.mark_retransmit(seq, now=now)
                self._logger.debug('Window full, retransmitting seq=%d', seq)
            else:
                # No unacked packets but window full? Shouldn't happen
                return True
        else:
            # Build new packet
            max_payload = self._transport.send_mtu - PACKET_HEADER_SIZE
            segments = self._collect_segments(max_payload)

            # If no data, check if keepalive needed
            if not segments:
                if now - self._last_send_time < self._keepalive_interval:
                    # No need to send yet
                    return True
                # Send keepalive ping
                segments = self._collect_segments(
                    max_payload,
                    keepalive_data=encode_message(tun_ping())
                )

            packet, seq = self._build_packet(segments=segments)
            packet_data = self._encode_packet(packet)
            self._send_window.send(segments, now=now)

        # Exchange with Bob
        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)

        try:
            response_data = self._transport.exchange(packet_data)
        except Exception as e:
            self._logger.warning('Exchange failed: %s', e)
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                self._set_state(TunnelState.CLOSED)
                self._logger.error('Connection lost after %d failures',
                                   self._max_failures)
            return False

        if response_data is None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                self._set_state(TunnelState.CLOSED)
            return False

        # Process response
        response = self._decode_packet(response_data)
        if response is None:
            return False

        self._bytes_received += len(response_data)
        self._last_recv_time = now
        self._consecutive_failures = 0

        rtt_samples = self._process_incoming_packet(response, now=now)

        # Update RTT estimator
        for sample in rtt_samples:
            self._rtt.add_sample(sample)

        return True

    def run(self, duration=None):
        """
        Run the tunnel for a duration or until closed.

        Args:
            duration: Max seconds to run (None = until closed)
        """
        start = time.time()
        while self._state == TunnelState.CONNECTED:
            self.poll()

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
