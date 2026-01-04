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
        self._fast_retransmit_enabled = config.tunnel_fast_retransmit_enabled
        self._fast_retransmit_min_age_ratio = (
            config.tunnel_fast_retransmit_min_age_ratio
        )
        self._fast_retransmit_max_per_seq = (
            config.tunnel_fast_retransmit_max_per_seq
        )
        self._fast_retransmit_counts = {}
        self._tick_epoch = 0
        self._backoff_epoch = None

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0

        # Timeout detection: packets sent without any response
        self._packets_since_response = 0
        self._max_packets_without_response = config.tunnel_timeout_packets

        # Track if Bob sent real data since the last poll we sent.
        self._got_data = False
        # Track keepalive-only responses (legacy "pong" terminology)
        self._last_was_pong_only = False
        self._pong_grace_polls = config.tunnel_pong_grace_polls
        min_grace = self._proposed_window * 2
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
        self._pacer_summary_interval = config.tunnel_pacer_summary_interval
        self._pacer_summary_last_time = None
        self._pacer_summary_last_sent = 0
        self._pacer_summary_last_recv = 0
        self._pacer_summary_last_stats = None
        self._pacer_target_sum = 0.0
        self._pacer_target_count = 0
        self._pacer_blocked_counts = {
            'window_distance': 0,
            'window_full': 0,
        }
        self._pacer_summary_last_blocked = None

        # Poll pacing (Alice only)
        self._poll_pacing_enabled = config.tunnel_poll_pacing_enabled
        self._poll_min_interval = config.tunnel_poll_min_interval
        self._poll_max_interval = config.tunnel_poll_max_interval
        self._poll_rtt_ratio = config.tunnel_poll_rtt_ratio
        self._next_poll_time = 0.0
        self._poll_pace_interval = None
        self._poll_pace_sleep_max = 0.01

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
            lambda: {
                'tx': self._proposed_send_mtu,
                'rx': self._proposed_recv_mtu,
                'side': 'alice',
            },
        )

        # Queue window request
        self.control.send_message(tun_window(self._proposed_window))
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window request',
            lambda: {
                'size': self._proposed_window,
                'side': 'alice',
            },
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
        serial_window = self._serial_window_negotiation()

        # 1. Receive all available responses
        received_any = False
        received_valid = False
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

        if self._fast_retransmit_enabled:
            self._prune_fast_retransmit_counts()

        # 2. Check for retransmits
        # Avoid RTO retransmits while ACKs are still advancing.
        ack_silence = None
        if self._last_cum_ack_time is not None:
            ack_silence = now - self._last_cum_ack_time
            if ack_silence < 0:
                ack_silence = 0.0
        if ack_silence is None or ack_silence >= self._rtt.rto_sec:
            retransmits = self._send_window.get_retransmits(
                self._rtt.rto_sec, now=now
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.retransmit_scan',
                'Retransmit scan',
                lambda: {
                    'count': len(retransmits),
                    'rto_sec': self._rtt.rto_sec,
                    'ack_silence': round(ack_silence, 6)
                    if ack_silence is not None else None,
                    'unacked': self._send_window.unacked_count,
                    'budget': self._retransmit_budget,
                    'side': 'alice',
                },
            )
            for seq, segments, flags, encrypted_body in retransmits:
                if flags & FLAG_KEEPALIVE:
                    dropped = self._send_window.drop_keepalive(
                        seq, reason='rto_keepalive', now=now
                    )
                    if dropped:
                        self._log_reliability_state(
                            logging.DEBUG,
                            'tunnel.keepalive_drop',
                            'Dropped keepalive retransmit',
                            now=now,
                            extra_fields={
                                'seq': seq,
                                'reason': 'rto_keepalive',
                            },
                        )
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
        else:
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.retransmit_skip',
                'Retransmit skipped due to ack silence',
                lambda: {
                    'reason': 'ack_silence',
                    'rto_sec': self._rtt.rto_sec,
                    'ack_silence': round(ack_silence, 6)
                    if ack_silence is not None else None,
                    'unacked': self._send_window.unacked_count,
                    'side': 'alice',
                },
            )

        self._maybe_fast_retransmit(now, ack_silence)

        # 3. Send new packets if we can
        pacing_blocked = False
        while True:
            if serial_window:
                if self._channel_manager.control_send_event.is_set():
                    if not self._can_send_new(
                            now=now,
                            keepalive_only=False):
                        break
                    if not self._poll_pacing_allows_send(now):
                        pacing_blocked = True
                        break
                    permit = self._reserve_transport_permit(now)
                    if permit is None:
                        break
                    segments = self._collect_segments(
                        self._send_mtu,
                        control_only=True,
                    )
                    if segments:
                        self._has_pending_data_acks = True
                        self._send_new_packet(segments, now, permit=permit)
                        continue
                    self._transport.release_send(permit)
            else:
                if self._channel_manager.has_pending_data():
                    if not self._can_send_new(
                            now=now,
                            keepalive_only=False):
                        break
                    if not self._poll_pacing_allows_send(now):
                        pacing_blocked = True
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
                dropped_seq = self._send_window.drop_oldest_keepalive(
                    reason='window_full', now=now
                )
                if dropped_seq is not None:
                    self._log_reliability_state(
                        logging.DEBUG,
                        'tunnel.keepalive_drop',
                        'Dropped keepalive due to window full',
                        now=now,
                        extra_fields={
                            'seq': dropped_seq,
                            'reason': 'window_full',
                        },
                    )
                if dropped_seq is None:
                    break
            if not self._can_send_new(
                    now=now,
                    keepalive_only=keepalive_due):
                break
            if not self._poll_pacing_allows_send(now):
                pacing_blocked = True
                break
            permit = self._reserve_transport_permit(now)
            if permit is None:
                break
            segments = self._collect_segments(
                self._send_mtu,
                control_only=serial_window,
            )
            if segments:
                self._send_new_packet(segments, now, permit=permit)
                continue
            if self._channel_manager.has_pending_data() and not serial_window:
                self._transport.release_send(permit)
                break
            self._send_new_packet([], now, flags=FLAG_KEEPALIVE, permit=permit)
            if consume_pong_grace and self._pong_grace_remaining > 0:
                self._pong_grace_remaining -= 1

        # 4. Opportunistically grow window after ACK progress or retry negotiation
        if self._window_growth_enabled:
            self._maybe_request_window(now)

        self._maybe_log_pacer_summary(time_provider.now())

        paced_sleep = False
        if pacing_blocked and self._packets_sent == packets_sent_before:
            paced_sleep = self._sleep_for_poll_pacing(time_provider.now())

        if (not paced_sleep and not received_any and
                self._packets_sent == packets_sent_before and
                not self._channel_manager.has_pending_data() and
                not self._got_data and not self._has_pending_data_acks):
            idle_sleep = max(self._config.tunnel_tick_sleep, 0.01)
            time_provider.sleep(idle_sleep)

        return True

    def _can_send_new(self, now=None, keepalive_only=False):
        """Check if we can send a new packet."""
        if now is None:
            now = time_provider.now()
        if self._serial_window_negotiation():
            if self._send_window.unacked_count > 0:
                return False
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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after send blocked',
                now=now,
                extra_fields={
                    'context': 'send_blocked',
                    'reason': 'window_full',
                    'keepalive_only': keepalive_only,
                },
            )
            self._note_pacer_blocked(
                'window_full',
                now,
                unacked=self._send_window.unacked_count,
            )
            return False
        pacer_cap = None
        if self._pacer.enabled:
            cap = self._pacer_cap()
            pacer_cap = min(
                self._send_window._max_in_flight,
                self._pacer.target_inflight(cap, srtt_ms=self._rtt.srtt_ms),
            )
        distance_info = self._send_window_distance_info()
        exceeded = False
        if distance_info is not None:
            distance = distance_info[0]
            distance_limit = distance_info[4]
            if distance >= distance_limit:
                exceeded = True
        if exceeded:
            (distance, max_in_flight, effective_cap, unacked,
             distance_limit, last_cum_ack, next_seq) = distance_info
            buffered = distance - unacked
            def build_distance_fields():
                fields = {
                    'distance': distance,
                    'distance_limit': distance_limit,
                    'buffered': buffered,
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
                    'effective_cap': effective_cap,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'alice',
                }
                if pacer_cap is not None:
                    fields['pacer_cap'] = pacer_cap
                fields.update(
                    self._send_window_distance_details(now, last_cum_ack)
                )
                return fields
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_window_distance',
                'Send window distance exceeded',
                build_distance_fields,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window distance exceeded',
                lambda: (
                    dict({
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
                    }, **({'pacer_cap': pacer_cap}
                          if pacer_cap is not None else {}))
                ),
            )
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after send blocked',
                now=now,
                extra_fields={
                    'context': 'send_blocked',
                    'reason': 'window_distance',
                    'keepalive_only': keepalive_only,
                    'distance': distance,
                    'distance_limit': distance_limit,
                },
            )
            self._note_pacer_blocked(
                'window_distance',
                now,
                unacked=unacked,
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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after send blocked',
                now=now,
                extra_fields={
                    'context': 'send_blocked',
                    'reason': 'rate_limit',
                    'keepalive_only': keepalive_only,
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
                    self._log_reliability_state(
                        logging.DEBUG,
                        'tunnel.reliability_state',
                        'Reliability state after send blocked',
                        now=now,
                        extra_fields={
                            'context': 'send_blocked',
                            'reason': 'pacer',
                            'keepalive_only': keepalive_only,
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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after retransmit blocked',
                now=now,
                extra_fields={
                    'context': 'retransmit_blocked',
                    'reason': 'retransmit_budget',
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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after retransmit blocked',
                now=now,
                extra_fields={
                    'context': 'retransmit_blocked',
                    'reason': 'rate_limit',
                },
            )
            return False
        return True

    def _fast_retransmit_sack_ready(self):
        return self._send_window.sack_progress_ready(self._last_cum_ack)

    def _prune_fast_retransmit_counts(self):
        if not self._fast_retransmit_counts:
            return
        unacked = self._send_window._unacked
        if not unacked:
            self._fast_retransmit_counts.clear()
            return
        valid = set(unacked.keys())
        stale = [
            seq for seq in self._fast_retransmit_counts
            if seq not in valid
        ]
        for seq in stale:
            del self._fast_retransmit_counts[seq]

    def _maybe_fast_retransmit(self, now, ack_silence):
        if not self._fast_retransmit_enabled:
            return False
        if ack_silence is None:
            return False
        if ack_silence >= self._rtt.rto_sec:
            return False
        if not self._fast_retransmit_sack_ready():
            return False
        exceeded, distance_info = self._send_window_distance_exceeded()
        if not exceeded:
            return False
        last_cum_ack = distance_info[5]
        missing_info = self._send_window.get_unacked_info(last_cum_ack)
        if missing_info is None:
            return False
        (seq, segments, flags, encrypted_body,
         send_time, _retransmit_count) = missing_info
        if send_time is None:
            return False
        missing_age = now - send_time
        if missing_age < 0:
            missing_age = 0.0
        min_age = self._rtt.rto_sec * self._fast_retransmit_min_age_ratio
        if missing_age < min_age:
            return False
        count = self._fast_retransmit_counts.get(seq, 0)
        if count >= self._fast_retransmit_max_per_seq:
            return False
        if not self._can_send_retransmit(now=now):
            return False
        sent = self._send_retransmit(
            seq,
            segments,
            flags,
            encrypted_body,
            now,
            reason='fast_retransmit',
        )
        if sent:
            self._fast_retransmit_counts[seq] = count + 1
        return sent

    def _serial_window_negotiation(self):
        return not self._window_negotiated

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
        return permit

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
        self._log_reliability_state(
            logging.DEBUG,
            'tunnel.reliability_state',
            'Reliability state after transport blocked',
            now=time_provider.now(),
            extra_fields={
                'context': 'send_blocked',
                'reason': 'transport',
            },
        )

    def _pacer_cap(self):
        cap = self._send_window._max_in_flight
        if cap < 1:
            cap = 1
        return cap

    def _poll_pacing_cap(self):
        cap = self._send_window._max_in_flight
        if cap < 1:
            cap = 1
        if hasattr(self._transport, 'max_in_flight'):
            try:
                transport_cap = self._transport.max_in_flight
            except Exception:
                transport_cap = None
            if transport_cap is not None and transport_cap > 0:
                if transport_cap < cap:
                    cap = transport_cap
        return cap

    def _poll_pacing_target_inflight(self, cap):
        if self._pacer is None:
            return cap
        return self._pacer.base_target_inflight(cap)

    def _poll_pacing_interval(self):
        if not self._poll_pacing_enabled:
            return None, None
        cap = self._poll_pacing_cap()
        target_inflight = self._poll_pacing_target_inflight(cap)
        if target_inflight < 1:
            target_inflight = 1
        srtt_ms = self._rtt.srtt_ms
        if srtt_ms is None:
            srtt_sec = self._keepalive_interval
        else:
            rtt_ms = srtt_ms
            if rtt_ms < self._config.tunnel_pace_rtt_floor_ms:
                rtt_ms = self._config.tunnel_pace_rtt_floor_ms
            srtt_sec = rtt_ms / 1000.0
            if srtt_sec <= 0:
                srtt_sec = self._keepalive_interval
        interval = (srtt_sec * self._poll_rtt_ratio) / float(target_inflight)
        min_interval = self._poll_min_interval
        max_interval = self._poll_max_interval
        if max_interval > self._keepalive_interval:
            max_interval = self._keepalive_interval
        if max_interval < min_interval:
            min_interval = max_interval
        if interval < min_interval:
            interval = min_interval
        if interval > max_interval:
            interval = max_interval
        self._maybe_log_poll_pace(interval, target_inflight, srtt_ms)
        return interval, target_inflight

    def _maybe_log_poll_pace(self, interval, target_inflight, srtt_ms):
        rounded = round(interval, 6)
        if self._poll_pace_interval == rounded:
            return
        self._poll_pace_interval = rounded
        pending = None
        if hasattr(self._transport, 'pending_count'):
            try:
                pending = self._transport.pending_count()
            except Exception:
                pending = None
        def build_fields():
            fields = {
                'side': 'alice',
                'interval': rounded,
                'target_inflight': target_inflight,
                'srtt_ms': srtt_ms,
            }
            if pending is not None:
                fields['pending'] = pending
            return fields
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.poll_pace',
            'Poll pacing interval updated',
            build_fields,
        )

    def _poll_pacing_allows_send(self, now):
        if not self._poll_pacing_enabled:
            return True
        if self._next_poll_time is None:
            return True
        return now >= self._next_poll_time

    def _advance_poll_pacing(self, now):
        if self._poll_pacing_enabled:
            interval, _ = self._poll_pacing_interval()
            if interval is not None:
                self._next_poll_time = now + interval
        self._got_data = False

    def _sleep_for_poll_pacing(self, now):
        if not self._poll_pacing_enabled:
            return False
        if self._next_poll_time is None:
            return False
        delay = self._next_poll_time - now
        if delay <= 0:
            return False
        sleep_time = min(delay, self._poll_pace_sleep_max)
        if sleep_time <= 0:
            return False
        time_provider.sleep(sleep_time)
        return True

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
        self._maybe_log_pacer_adjust(prev_target, fields)
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

    def _log_pacer_adjust(self, prev_target, reason, block_reason=None):
        if prev_target is None:
            return
        fields = self._pacer.state_fields(
            self._send_window.unacked_count,
            self._pacer_cap(),
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
        )
        def build_fields():
            event_fields = dict(fields)
            event_fields['previous_target_inflight'] = prev_target
            event_fields['side'] = 'alice'
            event_fields['reason'] = reason
            if block_reason is not None:
                event_fields['block_reason'] = block_reason
            return event_fields
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.pacer_adjust',
            'Pacer target decreased',
            build_fields,
        )

    def _maybe_log_pacer_adjust(self, prev_target, fields):
        if prev_target is None:
            return
        target = fields.get('target_inflight')
        if target is None or target >= prev_target:
            return
        if fields.get('block_penalty'):
            return
        feedback_target = fields.get('feedback_target')
        base_target = fields.get('base_target')
        baseline_target = fields.get('baseline_target')
        if (feedback_target is None or base_target is None or
                baseline_target is None):
            return
        if feedback_target >= base_target:
            return
        if baseline_target != feedback_target:
            return
        self._log_pacer_adjust(prev_target, 'feedback')

    def _note_pacer_blocked(self, reason, now, unacked=None):
        if reason in self._pacer_blocked_counts:
            self._pacer_blocked_counts[reason] += 1
        if not self._pacer.enabled:
            return
        cap = self._pacer_cap()
        srtt_ms = self._rtt.srtt_ms
        prev_target = self._pacer.target_inflight(cap, srtt_ms=srtt_ms)
        adjusted = self._pacer.on_blocked(
            reason,
            now,
            cap,
            srtt_ms=srtt_ms,
            unacked_count=unacked,
        )
        if not adjusted:
            return
        target = self._pacer.target_inflight(cap, srtt_ms=srtt_ms)
        if target >= prev_target:
            return
        self._log_pacer_adjust(prev_target, 'blocked', block_reason=reason)

    def _log_pacer_state(self, cap, unacked_count, action=None):
        if not self._pacer.enabled:
            return
        fields = self._pacer.state_fields(
            unacked_count,
            cap,
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
        )
        if self._pacer_summary_interval > 0:
            target = fields.get('target_inflight')
            if target is not None:
                self._pacer_target_sum += target
                self._pacer_target_count += 1
        fields['side'] = 'alice'
        if action is not None:
            fields['action'] = action
        def build_fields():
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.pacer_state',
            'Pacer state',
            build_fields,
        )

    def _maybe_log_pacer_summary(self, now):
        interval = self._pacer_summary_interval
        if interval <= 0:
            return
        if self._pacer_summary_last_time is None:
            self._pacer_summary_last_time = now
            self._pacer_summary_last_sent = self._packets_sent
            self._pacer_summary_last_recv = self._packets_received
            self._pacer_target_sum = 0.0
            self._pacer_target_count = 0
            self._pacer_summary_last_blocked = dict(
                self._pacer_blocked_counts
            )
            if self._stats_enabled:
                try:
                    self._pacer_summary_last_stats = (
                        self._reliability_stats.snapshot()
                    )
                except Exception:
                    self._pacer_summary_last_stats = None
            return
        elapsed = now - self._pacer_summary_last_time
        if elapsed < interval:
            return
        if elapsed <= 0:
            self._pacer_summary_last_time = now
            self._pacer_target_sum = 0.0
            self._pacer_target_count = 0
            return
        sent_delta = self._packets_sent - self._pacer_summary_last_sent
        recv_delta = self._packets_received - self._pacer_summary_last_recv
        send_rate = float(sent_delta) / elapsed
        recv_rate = float(recv_delta) / elapsed
        pending = None
        max_in_flight = None
        if hasattr(self._transport, 'pending_count'):
            try:
                pending = self._transport.pending_count()
            except Exception:
                pending = None
        if hasattr(self._transport, 'max_in_flight'):
            try:
                max_in_flight = self._transport.max_in_flight
            except Exception:
                max_in_flight = None

        pacer_fields = self._pacer.state_fields(
            self._send_window.unacked_count,
            self._pacer_cap(),
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
        )
        fields = {
            'side': 'alice',
            'state': self._state,
            'interval': round(elapsed, 6),
            'sent_delta': sent_delta,
            'recv_delta': recv_delta,
            'send_rate': round(send_rate, 6),
            'recv_rate': round(recv_rate, 6),
            'unacked': self._send_window.unacked_count,
            'send_window_max': self._send_window._max_in_flight,
            'pacer_enabled': self._pacer.enabled,
        }
        if pending is not None:
            fields['pending'] = pending
        if max_in_flight is not None:
            fields['transport_max_in_flight'] = max_in_flight
        if self._last_cum_ack_time is not None:
            ack_silence = now - self._last_cum_ack_time
            if ack_silence < 0:
                ack_silence = 0.0
            fields['ack_silence'] = round(ack_silence, 6)
        if self._last_ack_progress_time is not None:
            silence = now - self._last_ack_progress_time
            if silence < 0:
                silence = 0.0
            fields['ack_progress_silence'] = round(silence, 6)
        exceeded, distance_info = self._send_window_distance_exceeded()
        if exceeded:
            (distance, max_in_flight, effective_cap, unacked,
             distance_limit, last_cum_ack, next_seq) = distance_info
            fields.update({
                'distance': distance,
                'distance_limit': distance_limit,
                'distance_buffered': distance - unacked,
                'distance_unacked': unacked,
                'distance_effective_cap': effective_cap,
                'distance_last_cum_ack': last_cum_ack,
                'distance_next_seq': next_seq,
            })
        if pacer_fields:
            for key, value in pacer_fields.items():
                fields['pacer_' + key] = value
        if self._pacer_target_count > 0:
            avg_target = (
                self._pacer_target_sum / float(self._pacer_target_count)
            )
            fields['pacer_target_inflight_avg'] = round(avg_target, 6)
        if self._stats_enabled:
            try:
                stats_snapshot = self._reliability_stats.snapshot()
            except Exception:
                stats_snapshot = None
            if stats_snapshot:
                if self._pacer_summary_last_stats:
                    for key, value in stats_snapshot.items():
                        prev = self._pacer_summary_last_stats.get(key)
                        if prev is None:
                            continue
                        delta = value - prev
                        if delta:
                            fields['stat_delta_' + key] = delta
                self._pacer_summary_last_stats = stats_snapshot
        if self._pacer_summary_last_blocked is not None:
            for key, value in self._pacer_blocked_counts.items():
                prev = self._pacer_summary_last_blocked.get(key, 0)
                delta = value - prev
                if delta:
                    fields['blocked_' + key] = delta
            self._pacer_summary_last_blocked = dict(
                self._pacer_blocked_counts
            )
        def build_fields():
            return fields
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.pacer_summary',
            'Pacer summary',
            build_fields,
        )
        self._pacer_summary_last_time = now
        self._pacer_summary_last_sent = self._packets_sent
        self._pacer_summary_last_recv = self._packets_received
        self._pacer_target_sum = 0.0
        self._pacer_target_count = 0

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
        encrypted_body, packet_data = self._encode_packet_for_send(packet)

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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after send blocked',
                now=now,
                extra_fields={
                    'context': 'send_blocked',
                    'reason': 'rate_limit',
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
        self._advance_poll_pacing(now)
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
            lambda: self._packet_send_fields(
                packet,
                len(packet_data),
                'new',
            ),
        )

    def _send_retransmit(self, seq, segments, flags, encrypted_body, now, reason=None):
        """Retransmit a packet."""
        packet = self._rebuild_packet(seq, segments, flags=flags)
        encrypted_body, packet_data = self._encode_packet_for_send(
            packet,
            encrypted_body=encrypted_body,
        )

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
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after retransmit blocked',
                now=now,
                extra_fields={
                    'context': 'retransmit_blocked',
                    'reason': 'rate_limit',
                },
            )
            return False

        permit = self._reserve_transport_permit(now)
        if permit is None:
            self._reliability_stats.on_retransmit_skip_transport()
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after retransmit blocked',
                now=now,
                extra_fields={
                    'context': 'retransmit_blocked',
                    'reason': 'transport',
                },
            )
            return False

        prev_info = self._send_window.get_unacked_info(seq)
        prev_retransmit_count = None
        prev_age = None
        if prev_info is not None:
            prev_retransmit_count = prev_info[5]
            prev_send_time = prev_info[4]
            if prev_send_time is not None:
                prev_age = now - prev_send_time
                if prev_age < 0:
                    prev_age = 0.0
                prev_age = round(prev_age, 6)
        try:
            self._send_window.mark_retransmit(seq, now=now)
        except Exception:
            self._transport.release_send(permit)
            raise
        if self._pacer.enabled:
            self._pacer.on_retransmit(now)
        self._transport.send(packet_data, permit)
        self._advance_poll_pacing(now)

        self._consume_retransmit_budget()
        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        self._packets_since_response += 1
        def build_fields():
            fields = {
                'seq': seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(segments),
                'bytes': len(packet_data),
                'side': 'alice',
                'send_mtu': self._send_mtu,
                'recv_mtu': self._recv_mtu,
            }
            if reason is not None:
                fields['reason'] = reason
            if prev_retransmit_count is not None:
                fields['retransmit_count'] = prev_retransmit_count + 1
                fields['prev_retransmit_count'] = prev_retransmit_count
            if prev_age is not None:
                fields['prev_send_age'] = prev_age
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
            lambda: self._packet_send_fields(
                packet,
                len(packet_data),
                'retransmit',
            ),
        )
        self._log_reliability_state(
            logging.DEBUG,
            'tunnel.reliability_state',
            'Reliability state after retransmit',
            now=now,
            extra_fields={
                'context': 'retransmit_sent',
                'seq': seq,
                'reason': reason,
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
        if has_real_data:
            self._got_data = True

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
                self.control.send_message(tun_window(self._proposed_window))
                self._last_window_request_time = now
                log_event(
                    self._logger,
                    logging.INFO,
                    'tunnel.window_propose',
                    'Window request retry',
                    lambda: {
                        'size': self._proposed_window,
                        'reason': 'retry',
                        'side': 'alice',
                    },
                )
            return

        if not self._ack_progressed:
            return
        if now - self._last_window_request_time < self._window_growth_interval:
            return
        if self.negotiated_window >= self._proposed_window:
            return

        current = self.negotiated_window
        if self._window_growth_mode == 'doubling':
            requested = current * 2
        else:
            requested = current + self._window_growth_step

        requested = min(requested, self._proposed_window, self.MAX_WINDOW)
        if requested <= current:
            return

        self.control.send_message(tun_window(requested))
        self._last_window_request_time = now
        self._ack_progressed = False
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.window_propose',
            'Window growth request',
            lambda: {
                'size': requested,
                'current': current,
                'mode': self._window_growth_mode,
                'side': 'alice',
            },
        )

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
