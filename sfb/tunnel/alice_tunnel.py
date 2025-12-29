# -*- coding: ascii -*-
"""
Alice's tunnel implementation (client side).

Alice initiates the connection and polls Bob for data using a
pipelined request/response transport.
"""

from __future__ import absolute_import

import json
import logging
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
from ..logging_util import log_event


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
        self._payload_cap = getattr(transport, 'payload_cap', None)

        # Set proposed MTU from transport (for negotiation, asymmetric)
        send_payload = max(1, transport.send_mtu - PACKET_HEADER_SIZE)
        recv_payload = max(1, transport.recv_mtu - PACKET_HEADER_SIZE)
        self._proposed_send_mtu = send_payload
        self._proposed_recv_mtu = recv_payload

        # Use transport's actual limits before negotiation completes
        self._send_mtu = send_payload
        self._recv_mtu = recv_payload
        self._max_packet_size = recv_payload + PACKET_HEADER_SIZE

        # RTT estimation (Alice only)
        self._rtt = RttEstimator(
            initial_rto_ms=config.protocol_initial_rto_ms,
            min_rto_ms=config.protocol_min_rto_ms,
            max_rto_ms=config.protocol_max_rto_ms,
        )

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0

        # Timeout detection: packets sent without any response
        self._packets_since_response = 0
        self._max_packets_without_response = config.tunnel_timeout_packets

        # Adaptive polling: poll immediately when Bob sends real data
        self._got_data = False
        self._last_was_pong_only = False
        self._pong_grace_polls = config.tunnel_pong_grace_polls
        self._pong_grace_remaining = self._pong_grace_polls
        # Track if we have real data packets awaiting ACKs (not just keepalives)
        self._has_pending_data_acks = False
        # Window growth state (Alice only)
        self._window_growth_enabled = config.tunnel_window_growth_enabled
        self._window_growth_mode = config.tunnel_window_growth_mode
        self._window_growth_step = config.tunnel_window_growth_step
        self._window_growth_interval = config.tunnel_window_growth_interval
        self._last_window_request_time = 0
        self._last_ack_progress_time = 0
        self._ack_progressed = False

        # Enable module loader for handling Bob's module requests.
        self.enable_module_loader()

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
            # Check if tunnel was closed (e.g., by signal handler)
            if self._state == TunnelState.CLOSED:
                raise TunnelError('Tunnel closed during handshake')

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
                # Check if tunnel was closed during handshake
                if self._state == TunnelState.CLOSED:
                    raise TunnelError('Tunnel closed during handshake')
                self._logger.warning('Handshake error: %s', e)
                self._rtt.backoff()

            # Check state before sleeping
            if self._state == TunnelState.CLOSED:
                raise TunnelError('Tunnel closed during handshake')

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
            self._set_state(TunnelState.CONNECTED)
            self._last_recv_time = time.time()
            self._packets_since_response = 0

            # Retransmit final ACK until we see any response from Bob.
            start = time.time()
            while True:
                remaining = remaining_timeout - (time.time() - start)
                if remaining <= 0:
                    raise TunnelError('Handshake timeout')

                self._transport.send(ack_data)

                corr_id, response_data = self._transport.recv(
                    timeout=min(self._rtt.rto_sec, remaining)
                )

                if response_data:
                    response = self._decode_packet(response_data)
                    if response:
                        self._process_incoming_packet(response)
                        break

                self._rtt.backoff()

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
        # Queue MTU request (asymmetric)
        self.control.send_message(
            tun_mtu(self._proposed_send_mtu, self._proposed_recv_mtu)
        )
        self._logger.debug('Requesting MTU: tx=%d rx=%d',
                           self._proposed_send_mtu, self._proposed_recv_mtu)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_propose',
            'MTU request',
            {'tx': self._proposed_send_mtu, 'rx': self._proposed_recv_mtu},
        )

        # Queue window request
        self.control.send_message(tun_window(self._proposed_max_in_flight))
        self._logger.debug('Requesting window: %d', self._proposed_max_in_flight)
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window request',
            {'size': self._proposed_max_in_flight},
        )

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
        received_valid = False
        self._got_data = False  # Tracks data status of the most recent response
        last_resp_has_data = None
        while True:
            corr_id, data = self._transport.recv(timeout=0)
            if corr_id is None:
                break
            valid, has_data = self._handle_response(data, now)
            if valid:
                received_valid = True
                last_resp_has_data = has_data
            received_any = True

        if received_valid:
            self._packets_since_response = 0
            # Clear pending data flag if all data has been acked
            if self._send_window.unacked_count == 0:
                self._has_pending_data_acks = False
            if last_resp_has_data is not None:
                self._last_was_pong_only = not last_resp_has_data
                if last_resp_has_data:
                    self._pong_grace_remaining = self._pong_grace_polls

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
        while self._can_send_new():
            segments = self._collect_segments(self._send_mtu)
            is_real_data = bool(segments)
            if not segments:
                # No data to send - decide whether to poll Bob
                # If Bob's last response was pong-only, he has nothing to send,
                # so respect keepalive interval even if we have pending ACKs.
                # Poll immediately only if Bob sent real data (more might be coming).
                if self._last_was_pong_only:
                    if self._pong_grace_remaining > 0:
                        should_poll = True
                        self._pong_grace_remaining -= 1
                    else:
                        should_poll = now - self._last_send_time >= self._keepalive_interval
                else:
                    should_poll = self._got_data or self._has_pending_data_acks or (
                        now - self._last_send_time >= self._keepalive_interval
                    )
                if should_poll:
                    segments = self._collect_segments(
                        self._send_mtu,
                        keepalive_data=encode_message(tun_ping())
                    )
                if not segments:
                    break
            if is_real_data:
                self._has_pending_data_acks = True
            self._send_new_packet(segments, now)

        # 4. Opportunistically grow window after ACK progress or retry negotiation
        if self._window_growth_enabled:
            self._maybe_request_window(now)

        return True

    def _can_send_new(self):
        """Check if we can send a new packet."""
        if not self._send_window.can_send:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window full',
                {
                    'unacked': self._send_window.unacked_count,
                    'max_in_flight': self._send_window._max_in_flight,
                    'side': 'alice',
                },
            )
            return False
        can_send = self._transport.can_send()
        if not can_send:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Transport cannot send',
                {'side': 'alice'},
            )
        return can_send

    def _can_send_retransmit(self):
        """Check if we can send a retransmit packet."""
        can_send = self._transport.can_send()
        if not can_send:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Transport cannot send',
                {'side': 'alice'},
            )
        return can_send

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
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            {
                'seq': packet.seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(packet.segments),
                'bytes': len(packet_data),
                'side': 'alice',
            },
        )

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
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.retransmit',
            'Retransmitting packet',
            {'seq': seq, 'seg_count': len(segments), 'side': 'alice'},
        )
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            {
                'seq': packet.seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(packet.segments),
                'bytes': len(packet_data),
                'side': 'alice',
            },
        )

    def _handle_response(self, data, now):
        """Handle a transport response."""
        packet = self._decode_packet(data)
        if packet is None:
            return (False, False)

        self._bytes_received += len(data)
        self._last_recv_time = now

        # Check if packet contains real data (not just pong)
        # Real data = any data segment, or control messages other than pong.
        # Control segments carry one JSON message per line, not multiple.
        has_real_data = False
        for seg in packet.segments:
            if not seg.is_control:
                # Data segment - definitely real data
                has_real_data = True
            else:
                # Control segment - check if it's not just pong
                # Control data is newline-delimited JSON
                lines = seg.data.split(b'\n')
                for line in lines:
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode('ascii'))
                    except (ValueError, TypeError):
                        has_real_data = True
                        break
                    if msg.get('t') != 'tun' or msg.get('c') != 'pong':
                        has_real_data = True
                        break
        self._got_data = has_real_data

        prev_unacked = self._send_window.unacked_count
        rtt_samples = self._process_incoming_packet(packet, now=now)
        new_unacked = self._send_window.unacked_count
        if rtt_samples or new_unacked < prev_unacked:
            self._last_ack_progress_time = now
            self._ack_progressed = True

        for sample in rtt_samples:
            self._rtt.add_sample(sample)
        return (True, has_real_data)

    def _maybe_request_window(self, now):
        """Request a larger window if conditions allow."""
        # Retry initial negotiation even without ACK progress.
        if not self._window_negotiated:
            if now - self._last_window_request_time >= self._window_growth_interval:
                self.control.send_message(tun_window(self._proposed_max_in_flight))
                self._last_window_request_time = now
            return

        if not self._ack_progressed:
            return
        if now - self._last_window_request_time < self._window_growth_interval:
            return
        if self._negotiated_window >= self._proposed_max_in_flight:
            return

        current = self._negotiated_window
        if self._window_growth_mode == 'doubling':
            requested = current * 2
        else:
            requested = current + self._window_growth_step

        requested = min(requested, self._proposed_max_in_flight, self.MAX_WINDOW)
        if requested <= current:
            return

        self.control.send_message(tun_window(requested))
        self._last_window_request_time = now
        self._ack_progressed = False

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
            time.sleep(self._config.tunnel_tick_sleep)

    def _run_loop(self):
        """Background thread loop - calls tick() until stopped."""
        while not self._bg_stop and self._state == TunnelState.CONNECTED:
            try:
                self.tick()
            except Exception as e:
                self._logger.warning('Tick error: %s', e)
            time.sleep(self._config.tunnel_tick_sleep)

    def close(self):
        """Close the tunnel and transport."""
        super(AliceTunnel, self).close()
        try:
            self._transport.close()
        except Exception:
            pass
