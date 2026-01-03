# -*- coding: ascii -*-
"""
Alice's tunnel implementation (client side).

Alice initiates the connection and polls Bob for data using a
pipelined request/response transport.
"""

from __future__ import absolute_import

import json
import logging

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from .tunnel_control_messages import (
    tun_mtu,
    tun_window,
)
from .pacing import AdaptivePacer
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
)
from ..reliability import RttEstimator
from ..transport.transport_base import RateLimiter
from ..logging_util import log_event
from .. import time_provider


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
        self._init_transport_limits(transport)

        # RTT estimation (Alice only)
        self._rtt = RttEstimator(
            initial_rto_ms=config.protocol_initial_rto_ms,
            min_rto_ms=config.protocol_min_rto_ms,
            max_rto_ms=config.protocol_max_rto_ms,
        )
        self._retransmit_cap = config.tunnel_retransmit_cap
        self._retransmit_budget = self._retransmit_cap
        self._tick_epoch = 0
        self._backoff_epoch = None

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0

        # Timeout detection: packets sent without any response
        self._packets_since_response = 0
        self._max_packets_without_response = config.tunnel_timeout_packets

        # Adaptive polling: poll immediately when Bob sends real data
        self._got_data = False
        # Track keepalive-only responses (legacy "pong" terminology)
        self._last_was_pong_only = False
        self._pong_grace_polls = config.tunnel_pong_grace_polls
        min_grace = self._proposed_max_in_flight * 2
        if self._pong_grace_polls < min_grace:
            self._pong_grace_polls = min_grace
        self._pong_grace_remaining = self._pong_grace_polls
        # Track if we have real data packets awaiting ACKs (not just keepalives)
        self._has_pending_data_acks = False
        # Window growth state (Alice only)
        self._window_growth_enabled = config.tunnel_window_growth_enabled
        self._window_growth_mode = config.tunnel_window_growth_mode
        self._window_growth_step = config.tunnel_window_growth_step
        self._window_growth_interval = config.tunnel_window_growth_interval
        self._last_window_request_time = 0
        self._ack_progressed = False
        # Transport-agnostic send rate limiter
        self._send_limiter = RateLimiter(
            config.tunnel_send_rate,
            burst=config.tunnel_send_burst,
        )
        # Adaptive pacing (Alice only)
        self._pacer = AdaptivePacer(
            config.tunnel_adaptive_pacing_enabled,
            config.tunnel_pace_target_inflight_ratio,
            config.tunnel_pace_min_inflight,
            config.tunnel_pace_max_inflight,
            config.tunnel_pace_feedback_gain,
            config.tunnel_pace_ack_ewma_alpha,
            config.tunnel_pace_rtt_floor_ms,
            config.tunnel_pace_ack_idle_reset_sec,
        )
        self._pacer_last_target = None

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

        start_time = time_provider.now()
        attempt = 0

        while time_provider.now() - start_time < timeout:
            # Check if tunnel was closed (e.g., by signal handler)
            if self._state == TunnelState.CLOSED:
                raise TunnelError('Tunnel closed during handshake')

            attempt += 1
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.handshake_attempt',
                'Handshake attempt',
                lambda: {'attempt': attempt, 'side': 'alice'},
            )

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
                permit = self._transport.reserve_send(now=time_provider.now())
                if permit is None:
                    self._log_transport_blocked()
                    time_provider.sleep(min(self._rtt.rto_sec, timeout / 10))
                    continue
                self._transport.send(syn_data, permit)

                # Wait for SYN+ACK
                remaining = timeout - (time_provider.now() - start_time)
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
                        self._complete_handshake(timeout - (time_provider.now() - start_time))
                        return

                self._rtt.backoff()

            except Exception as e:
                # Check if tunnel was closed during handshake
                if self._state == TunnelState.CLOSED:
                    raise TunnelError('Tunnel closed during handshake')
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.handshake_error',
                    'Handshake error',
                    lambda: {'error': str(e), 'side': 'alice'},
                )
                self._rtt.backoff()

            # Check state before sleeping
            if self._state == TunnelState.CLOSED:
                raise TunnelError('Tunnel closed during handshake')

            # Wait before retry
            time_provider.sleep(min(self._rtt.rto_sec, timeout / 10))

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
            self._last_recv_time = time_provider.now()
            self._packets_since_response = 0

            # Retransmit final ACK until we see any response from Bob.
            start = time_provider.now()
            while True:
                remaining = remaining_timeout - (time_provider.now() - start)
                if remaining <= 0:
                    raise TunnelError('Handshake timeout')

                permit = self._transport.reserve_send(now=time_provider.now())
                if permit is None:
                    self._log_transport_blocked()
                    time_provider.sleep(min(self._rtt.rto_sec, remaining))
                    continue
                self._transport.send(ack_data, permit)

                corr_id, response_data = self._transport.recv(
                    timeout=min(self._rtt.rto_sec, remaining)
                )

                if response_data:
                    response = self._decode_packet(response_data)
                    if response:
                        self._process_incoming_packet(response)
                        break

                self._rtt.backoff()

            log_event(
                self._logger,
                logging.INFO,
                'tunnel.connected',
                'Connected',
                lambda: {
                    'local_isn': self._local_isn,
                    'remote_isn': self._remote_isn,
                    'mode': 'syn_ack',
                    'side': 'alice',
                },
            )

            self._rtt.reset()
            # Initiate MTU and window negotiation
            self._send_negotiation()

        except Exception as e:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.ack_send_failed',
                'Failed to send ACK',
                lambda: {'error': str(e), 'side': 'alice'},
            )
            # Still mark as connected - Bob will accept data as implicit ACK
            self._set_state(TunnelState.CONNECTED)
            self._rtt.reset()
            # Still try to negotiate
            self._send_negotiation()

    def _send_negotiation(self):
        """Queue MTU and window negotiation messages."""
        # Queue MTU request (asymmetric)
        self.control.send_message(
            tun_mtu(self._proposed_send_mtu, self._proposed_recv_mtu)
        )
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_propose',
            'MTU request',
            lambda: {'tx': self._proposed_send_mtu, 'rx': self._proposed_recv_mtu},
        )

        # Queue window request
        self.control.send_message(tun_window(self._proposed_max_in_flight))
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window request',
            lambda: {'size': self._proposed_max_in_flight},
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

        now = time_provider.now()
        self._tick_epoch += 1
        self._retransmit_budget = self._retransmit_cap
        packets_sent_before = self._packets_sent

        # 1. Receive all available responses
        received_any = False
        received_valid = False
        self._got_data = False  # Tracks data status of the most recent response
        last_resp_has_data = None
        while True:
            corr_id, data = self._transport.recv(timeout=self._config.non_blocking_poll_timeout)
            if corr_id is None:
                break
            valid, has_data = self._handle_response(data, now)
            if valid:
                received_valid = True
                last_resp_has_data = has_data
            received_any = True

        # If pending is high and we didn't receive anything, wait briefly for responses
        # This avoids busy-polling when the pending queue is near capacity
        if not received_any and hasattr(self._transport, 'pending_count'):
            pending = self._transport.pending_count()
            cap = getattr(self._transport, 'max_in_flight', None)
            if cap is None:
                cap = self._send_window._max_in_flight
            threshold = int(cap * 0.75)
            if pending >= threshold:
                corr_id, data = self._transport.recv(timeout=0.05)
                if corr_id is not None:
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
            log_event(
                self._logger,
                logging.ERROR,
                'tunnel.timeout_packets',
                'Connection timeout after packets without response',
                lambda: {
                    'count': self._max_packets_without_response,
                    'side': 'alice',
                },
            )
            return False

        # 2. Check for retransmits
        # Avoid RTO retransmits while ACKs are still advancing.
        ack_silence = None
        if self._last_cum_ack_time is not None:
            ack_silence = now - self._last_cum_ack_time
        if ack_silence is None or ack_silence >= self._rtt.rto_sec:
            retransmits = self._send_window.get_retransmits(
                self._rtt.rto_sec, now=now
            )
            for seq, segments, flags, encrypted_body in retransmits:
                if flags & FLAG_KEEPALIVE:
                    self._send_window.drop_keepalive(seq)
                    continue
                if not self._can_send_retransmit(now=now):
                    break
                sent = self._send_retransmit(
                    seq,
                    segments,
                    flags,
                    encrypted_body,
                    now,
                    reason='rto',
                )
                if sent:
                    self._backoff_rto_once()

        # 3. Send new packets if we can
        while True:
            if self._channel_manager.has_pending_data():
                if not self._can_send_new(
                        now=now,
                        keepalive_only=False):
                    break
                permit = self._reserve_transport_permit(now)
                if permit is None:
                    break
                segments = self._collect_segments(self._send_mtu)
                if segments:
                    self._has_pending_data_acks = True
                    self._send_new_packet(segments, now, permit=permit)
                    continue
                self._transport.release_send(permit)
                break

            should_poll, keepalive_due, consume_pong_grace = self._poll_decision(now)
            if not should_poll:
                break
            if keepalive_due and not self._send_window.can_send:
                if not self._send_window.drop_oldest_keepalive():
                    break
            if not self._can_send_new(
                    now=now,
                    keepalive_only=keepalive_due):
                break
            permit = self._reserve_transport_permit(now)
            if permit is None:
                break
            segments = self._collect_segments(self._send_mtu)
            if segments:
                self._send_new_packet(segments, now, permit=permit)
                continue
            if self._channel_manager.has_pending_data():
                self._transport.release_send(permit)
                break
            self._send_new_packet([], now, flags=FLAG_KEEPALIVE, permit=permit)
            if consume_pong_grace and self._pong_grace_remaining > 0:
                self._pong_grace_remaining -= 1

        # 4. Opportunistically grow window after ACK progress or retry negotiation
        if self._window_growth_enabled:
            self._maybe_request_window(now)

        if (not received_any and self._packets_sent == packets_sent_before and
                not self._channel_manager.has_pending_data() and
                not self._got_data and not self._has_pending_data_acks):
            idle_sleep = max(self._config.tunnel_tick_sleep, 0.01)
            time_provider.sleep(idle_sleep)

        return True

    def _can_send_new(self, now=None, keepalive_only=False):
        """Check if we can send a new packet."""
        if now is None:
            now = time_provider.now()
        if not self._send_window.can_send:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window full',
                lambda: {
                    'unacked': self._send_window.unacked_count,
                    'max_in_flight': self._send_window._max_in_flight,
                    'side': 'alice',
                },
            )
            return False
        effective_cap = None
        if self._pacer.enabled:
            cap = self._pacer_cap()
            effective_cap = min(
                self._send_window._max_in_flight,
                self._pacer.target_inflight(cap, srtt_ms=self._rtt.srtt_ms),
            )
        exceeded, distance_info = self._send_window_distance_exceeded(
            cap_override=effective_cap
        )
        if exceeded:
            (distance, max_in_flight, effective_cap, unacked,
             distance_limit, last_cum_ack, next_seq) = distance_info
            buffered = distance - unacked
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_window_distance',
                'Send window distance exceeded',
                lambda: {
                    'distance': distance,
                    'distance_limit': distance_limit,
                    'buffered': buffered,
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
                    'effective_cap': effective_cap,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'alice',
                },
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window distance exceeded',
                lambda: {
                    'distance': distance,
                    'distance_limit': distance_limit,
                    'buffered': buffered,
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
                    'effective_cap': effective_cap,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'alice',
                    'reason': 'window_distance',
                },
            )
            return False
        if self._send_limiter is not None and not self._send_limiter.can_send(now=now):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send rate limited',
                lambda: {
                    'side': 'alice',
                    'rate': self._config.tunnel_send_rate,
                    'burst': self._config.tunnel_send_burst,
                },
            )
            return False
        if self._pacer.enabled:
            cap = self._pacer_cap()
            self._maybe_log_pacer_target_change(cap, reason='gate_check')
            if not keepalive_only:
                unacked = self._send_window.unacked_count
                if not self._pacer.can_send(unacked, cap, srtt_ms=self._rtt.srtt_ms):
                    self._log_pacer_state(cap, unacked, action='blocked')
                    log_event(
                        self._logger,
                        logging.DEBUG,
                        'tunnel.send_blocked',
                        'Send pacing blocked',
                        lambda: {
                            'side': 'alice',
                            'reason': 'pacer',
                            'unacked': unacked,
                            'cap': cap,
                        },
                    )
                    return False
        return True

    def _can_send_retransmit(self, now=None):
        """Check if we can send a retransmit packet."""
        if now is None:
            now = time_provider.now()
        if self._retransmit_budget is not None and self._retransmit_budget <= 0:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Retransmit budget exhausted',
                lambda: {
                    'side': 'alice',
                    'reason': 'retransmit_budget',
                    'cap': self._retransmit_cap,
                },
            )
            return False
        if self._send_limiter is not None and not self._send_limiter.can_send(now=now):
            self._reliability_stats.on_retransmit_skip_rate_limit()
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Retransmit rate limited',
                lambda: {
                    'side': 'alice',
                    'rate': self._config.tunnel_send_rate,
                    'burst': self._config.tunnel_send_burst,
                },
            )
            return False
        return True

    def _consume_retransmit_budget(self):
        if self._retransmit_budget is None:
            return
        if self._retransmit_budget > 0:
            self._retransmit_budget -= 1

    def _backoff_rto_once(self):
        if self._backoff_epoch == self._tick_epoch:
            return
        self._rtt.backoff()
        self._backoff_epoch = self._tick_epoch

    def _reserve_transport_permit(self, now):
        permit = self._transport.reserve_send(now=now)
        if permit is None:
            self._log_transport_blocked()
            return None
        if self._transport_headroom_blocked(permit):
            return None
        return permit

    def _transport_headroom(self, max_in_flight):
        if max_in_flight is None:
            return 0
        headroom = max(2, max_in_flight // 16)
        if max_in_flight <= headroom:
            return 0
        return headroom

    def _transport_headroom_blocked(self, permit):
        if not hasattr(self._transport, 'pending_count'):
            return False
        max_in_flight = getattr(self._transport, 'max_in_flight', None)
        headroom = self._transport_headroom(max_in_flight)
        if headroom <= 0:
            return False
        try:
            pending = permit.pending_before
            if pending is None:
                pending = self._transport.pending_count()
        except Exception:
            return False
        limit = max_in_flight - headroom
        if pending < limit:
            return False
        try:
            self._transport.release_send(permit)
        except Exception:
            pass
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.send_blocked',
            'Transport headroom reserved',
            lambda: {
                'side': 'alice',
                'reason': 'transport_headroom',
                'pending': pending,
                'max_in_flight': max_in_flight,
                'headroom': headroom,
                'limit': limit,
            },
        )
        return True

    def _log_transport_blocked(self):
        def build_fields():
            fields = {'side': 'alice'}
            if hasattr(self._transport, 'pending_count'):
                try:
                    fields['pending'] = self._transport.pending_count()
                except Exception:
                    pass
            if hasattr(self._transport, 'max_in_flight'):
                fields['max_in_flight'] = self._transport.max_in_flight
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.send_blocked',
            'Transport cannot send',
            build_fields,
        )

    def _pacer_cap(self):
        cap = self._send_window._max_in_flight
        if cap < 1:
            cap = 1
        return cap

    def _maybe_log_pacer_target_change(self, cap, reason=None):
        if not self._pacer.enabled:
            return
        fields = self._pacer.state_fields(
            self._send_window.unacked_count,
            cap,
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
        )
        target = fields.get('target_inflight')
        if target is None:
            return
        if self._pacer_last_target == target:
            return
        prev_target = self._pacer_last_target
        self._pacer_last_target = target
        def build_fields():
            event_fields = dict(fields)
            event_fields['previous_target_inflight'] = prev_target
            event_fields['side'] = 'alice'
            if reason is not None:
                event_fields['reason'] = reason
            return event_fields
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.pacer_target',
            'Pacer target adjusted to %s' % target,
            build_fields,
        )

    def _log_pacer_state(self, cap, unacked_count, action=None):
        if not self._pacer.enabled:
            return
        def build_fields():
            fields = self._pacer.state_fields(
                unacked_count,
                cap,
                rate_limit=self._config.tunnel_send_rate,
                srtt_ms=self._rtt.srtt_ms,
            )
            fields['side'] = 'alice'
            if action is not None:
                fields['action'] = action
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.pacer_state',
            'Pacer state',
            build_fields,
        )

    def _poll_decision(self, now):
        if self._last_was_pong_only:
            if self._pong_grace_remaining > 0:
                return True, False, True
            return (
                now - self._last_send_time >= self._keepalive_interval,
                True,
                False,
            )
        if self._got_data or self._has_pending_data_acks:
            return True, False, False
        return (
            now - self._last_send_time >= self._keepalive_interval,
            True,
            False,
        )

    def _send_new_packet(self, segments, now, flags=0, permit=None):
        """Send a new packet with given segments."""
        packet, seq = self._build_packet(flags=flags, segments=segments)
        body = self._encode_segments(packet.segments)
        encrypted_body = self._encrypt(
            body,
            seq=packet.seq,
            direction=self._direction_outbound(),
        )
        packet_data = self._encode_packet(packet, encrypted_body=encrypted_body)

        if self._send_limiter is not None and not self._send_limiter.consume(now=now):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send rate limited before transmit',
                lambda: {
                    'side': 'alice',
                    'rate': self._config.tunnel_send_rate,
                    'burst': self._config.tunnel_send_burst,
                },
            )
            if permit is not None:
                self._transport.release_send(permit)
            return

        if permit is None:
            permit = self._reserve_transport_permit(now)
            if permit is None:
                return

        try:
            self._send_window.send(
                segments,
                flags=flags,
                encrypted_body=encrypted_body,
                now=now,
            )
        except Exception:
            self._transport.release_send(permit)
            raise
        self._transport.send(packet_data, permit)
        if self._pacer.enabled:
            cap = self._pacer_cap()
            self._log_pacer_state(cap, self._send_window.unacked_count, action='send')

        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        self._packets_since_response += 1
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
                'bytes': len(packet_data),
                'side': 'alice',
            },
        )

    def _send_retransmit(self, seq, segments, flags, encrypted_body, now, reason=None):
        """Retransmit a packet."""
        packet = self._rebuild_packet(seq, segments, flags=flags)
        if encrypted_body is None:
            body = self._encode_segments(packet.segments)
            encrypted_body = self._encrypt(
                body,
                seq=seq,
                direction=self._direction_outbound(),
            )
        packet_data = self._encode_packet(packet, encrypted_body=encrypted_body)

        if self._send_limiter is not None and not self._send_limiter.consume(now=now):
            self._reliability_stats.on_retransmit_skip_rate_limit()
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Retransmit rate limited before transmit',
                lambda: {
                    'side': 'alice',
                    'rate': self._config.tunnel_send_rate,
                    'burst': self._config.tunnel_send_burst,
                },
            )
            return False

        permit = self._reserve_transport_permit(now)
        if permit is None:
            self._reliability_stats.on_retransmit_skip_transport()
            return False

        try:
            self._send_window.mark_retransmit(seq, now=now)
        except Exception:
            self._transport.release_send(permit)
            raise
        if self._pacer.enabled:
            self._pacer.on_retransmit(now)
        self._transport.send(packet_data, permit)

        self._consume_retransmit_budget()
        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        self._packets_since_response += 1
        def build_fields():
            fields = {'seq': seq, 'seg_count': len(segments), 'side': 'alice'}
            if reason is not None:
                fields['reason'] = reason
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.retransmit',
            'Retransmitting packet',
            build_fields,
        )
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
                'bytes': len(packet_data),
                'side': 'alice',
            },
        )
        return True

    def _handle_response(self, data, now):
        """Handle a transport response."""
        packet, packet_size = self._decode_packet(data, return_size=True)
        if packet is None:
            return (False, False)

        self._bytes_received += len(data)
        self._last_recv_time = now

        # Check if packet contains real data.
        # Real data = any data segment, or control messages other than legacy pong.
        # Keepalive-flag packets have no segments and are not real data.
        # Control segments carry one JSON message per line, not multiple.
        has_real_data = False
        if not (packet.flags & FLAG_KEEPALIVE):
            for seg in packet.segments:
                if not seg.is_control:
                    # Data segment - definitely real data
                    has_real_data = True
                else:
                    # Control segment - check if it's not just legacy pong
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
        rtt_samples, acked_count, data_acked_count = self._process_incoming_packet(
            packet, now=now, packet_size=packet_size
        )
        new_unacked = self._send_window.unacked_count
        if rtt_samples or acked_count > 0:
            self._last_ack_progress_time = now
            self._ack_progressed = True
        for sample in rtt_samples:
            self._rtt.add_sample(sample)
        if self._pacer.enabled and data_acked_count > 0:
            self._pacer.on_ack(
                data_acked_count,
                now,
                srtt_ms=self._rtt.srtt_ms,
            )
            self._maybe_log_pacer_target_change(self._pacer_cap(), reason='ack')
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
        start = time_provider.now()
        while self._state == TunnelState.CONNECTED:
            self.tick()

            if duration and (time_provider.now() - start) >= duration:
                break

            # Brief sleep to avoid busy loop
            time_provider.sleep(self._config.tunnel_tick_sleep)

    def _run_loop(self):
        """Background thread loop - calls tick() until stopped."""
        while not self._bg_stop and self._state == TunnelState.CONNECTED:
            try:
                self.tick()
            except Exception as e:
                log_event(
                    self._logger,
                    logging.WARNING,
                    'tunnel.tick_error',
                    'Tick error',
                    lambda: {'error': str(e), 'side': 'alice'},
                    exc_info=True,
                )
            time_provider.sleep(self._config.tunnel_tick_sleep)

    def close(self):
        """Close the tunnel and transport."""
        super(AliceTunnel, self).close()
        try:
            self._transport.close()
        except Exception:
            pass
