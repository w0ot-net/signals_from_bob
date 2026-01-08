# -*- coding: ascii -*-
"""
Alice's tunnel implementation (client side).

Alice initiates the connection and polls Bob for data using a
pipelined request/response transport.
"""

from __future__ import absolute_import

import logging

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from .tunnel_control_messages import (
    tun_mtu,
    tun_window,
)
from ..reliability import AdaptivePacer
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
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

        # Timeout detection: seconds without any response
        self._no_response_timeout = config.tunnel_no_response_timeout

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
        self._tick_sleep_hint = 0.0

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

        start_time = time_provider.now()
        attempt = 0

        while time_provider.now() - start_time < timeout:
            # Check if tunnel was closed (e.g., by signal handler)
            if self._state == TunnelState.CLOSED:
                raise TunnelError('Tunnel closed during handshake')
            if self._state != TunnelState.CONNECTING or self._local_isn is None:
                self._set_state(TunnelState.CONNECTING)
                self._local_isn = self._generate_isn()
                self._remote_isn = None
                self._send_window._next_seq = self._local_isn
                self._recv_window.set_initial_seq(0)

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
                permit = self._reserve_transport_permit(
                    time_provider.now(),
                    has_data_pending=False,
                )
                if permit is None:
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
            self._set_state(TunnelState.HANDSHAKE_ACKED)
            self._last_recv_time = time_provider.now()

            # Retransmit final ACK until we see the first post-ACK response.
            start = time_provider.now()
            while True:
                remaining = remaining_timeout - (time_provider.now() - start)
                if remaining <= 0:
                    self._set_state(TunnelState.DISCONNECTED)
                    raise TunnelError('Handshake timeout')

                permit = self._reserve_transport_permit(
                    time_provider.now(),
                    has_data_pending=False,
                )
                if permit is None:
                    time_provider.sleep(min(self._rtt.rto_sec, remaining))
                    continue
                self._transport.send(ack_data, permit)

                corr_id, response_data = self._transport.recv(
                    timeout=min(self._rtt.rto_sec, remaining)
                )

                if response_data:
                    response = self._decode_packet(response_data)
                    if response:
                        if response.flags & (FLAG_SYN | FLAG_ACK):
                            log_event(
                                self._logger,
                                logging.DEBUG,
                                'tunnel.handshake_late_packet',
                                'Ignored stale handshake packet',
                                lambda: {
                                    'flags': response.flags,
                                    'side': 'alice',
                                },
                            )
                            continue
                        self._process_incoming_packet(response)
                        self._set_state(TunnelState.CONNECTED)
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
            self._set_state(TunnelState.DISCONNECTED)
            raise

    def _send_negotiation(self):
        """Queue MTU and window negotiation messages."""
        # Queue MTU request (asymmetric)
        send_payload = self._payload_mtu_from_packet(
            self._proposed_send_packet_mtu
        )
        recv_payload = self._payload_mtu_from_packet(
            self._proposed_recv_packet_mtu
        )
        self.control.send_message(
            tun_mtu(send_payload, recv_payload)
        )
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.mtu_propose',
            'MTU request',
            lambda: {
                'tx_payload': send_payload,
                'rx_payload': recv_payload,
                'tx_packet_mtu': self._proposed_send_packet_mtu,
                'rx_packet_mtu': self._proposed_recv_packet_mtu,
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
        pending_event = self._channel_manager.pending_send_event
        control_send_event = self._channel_manager.control.send_event
        data_send_event = self._channel_manager.data_send_event
        def pending_mode_set(mode):
            if mode == 'control':
                return control_send_event.is_set()
            if mode == 'data':
                return data_send_event.is_set()
            return pending_event.is_set()

        # 1. Receive all available responses
        received_any = False
        received_valid = False
        last_resp_kind = None
        while True:
            corr_id, data = self._transport.recv(timeout=self._config.non_blocking_poll_timeout)
            if corr_id is None:
                break
            valid, resp_kind = self._handle_response(data, now)
            if valid:
                received_valid = True
                if resp_kind is not None:
                    last_resp_kind = resp_kind
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
                    valid, resp_kind = self._handle_response(data, now)
                    if valid:
                        received_valid = True
                        if resp_kind is not None:
                            last_resp_kind = resp_kind
                    received_any = True

        if received_valid:
            # Clear pending data flag if all data has been acked
            if self._send_window.data_unacked_count() == 0:
                self._has_pending_data_acks = False
            if last_resp_kind is not None:
                self._last_was_pong_only = (last_resp_kind == 'keepalive')
                if last_resp_kind == 'has_segments':
                    self._pong_grace_remaining = self._pong_grace_polls

        # Check connection timeout (no response from Bob)
        if self._last_recv_time:
            silence = now - self._last_recv_time
            if silence < 0:
                silence = 0.0
            if silence >= self._no_response_timeout:
                self._set_state(TunnelState.CLOSED)
                log_event(
                    self._logger,
                    logging.ERROR,
                    'tunnel.timeout_no_response',
                    'Connection timeout after no response',
                    lambda: {
                        'elapsed': round(silence, 3),
                        'timeout': self._no_response_timeout,
                        'side': 'alice',
                    },
                )
                return False

        if self._fast_retransmit_enabled:
            self._prune_fast_retransmit_counts()

        # 2. Check for retransmits
        # Avoid RTO retransmits while ACKs are still advancing.
        ack_silence = self._send_window.ack_silence(now=now)
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
                if not self._can_send_retransmit(now=now):
                    break
                reason = 'rto_keepalive' if flags & FLAG_KEEPALIVE else 'rto'
                sent = self._send_retransmit(
                    seq,
                    segments,
                    flags,
                    encrypted_body,
                    now,
                    reason=reason,
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
        send_payload_limit = self._payload_mtu_from_packet(
            self._send_packet_mtu
        )
        pacing_blocked = False
        tick_slept = False
        pending_mode = 'control' if serial_window else 'control_or_data'
        control_only = serial_window
        break_on_empty = not serial_window
        while True:
            if pending_mode_set(pending_mode):
                if not self._can_send_new(
                        now=now,
                        keepalive_only=False):
                    break
                if not self._poll_pacing_allows_send(now):
                    pacing_blocked = True
                    break
                has_data_pending = data_send_event.is_set()
                permit = self._reserve_transport_permit(
                    now,
                    has_data_pending=has_data_pending,
                )
                if permit is None:
                    break
                payload_cap = self._transport.payload_cap_for_send(permit)
                segments = self._collect_segments(
                    send_payload_limit,
                    control_only=control_only,
                    payload_cap=payload_cap,
                )
                if segments:
                    self._has_pending_data_acks = True
                    self._send_new_packet(segments, now, permit=permit)
                    continue
                self._transport.release_send(permit)
                if break_on_empty:
                    break

            should_poll, keepalive_due, consume_pong_grace = self._poll_decision(now)
            if not should_poll:
                break
            if pending_mode_set(pending_mode):
                continue
            window_full = not self._send_window.can_send
            if window_full:
                if not self._can_send_new(
                        now=now,
                        keepalive_only=keepalive_due,
                        allow_window_full=True):
                    break
            else:
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
            payload_cap = self._transport.payload_cap_for_send(permit)
            if window_full:
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
                    self._transport.release_send(permit)
                    break
                self._send_new_packet(
                    [],
                    now,
                    flags=FLAG_KEEPALIVE,
                    permit=permit,
                )
                if consume_pong_grace and self._pong_grace_remaining > 0:
                    self._pong_grace_remaining -= 1
                continue
            segments = self._collect_segments(
                send_payload_limit,
                control_only=control_only,
                payload_cap=payload_cap,
            )
            if segments:
                self._send_new_packet(segments, now, permit=permit)
                continue
            if break_on_empty and pending_mode_set(pending_mode):
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
            if paced_sleep:
                tick_slept = True

        if (not paced_sleep and not received_any and
                self._packets_sent == packets_sent_before and
                not pending_mode_set('control_or_data') and
                not self._got_data and not self._has_pending_data_acks):
            idle_sleep = max(self._config.tunnel_tick_sleep, 0.01)
            time_provider.sleep(idle_sleep)
            tick_slept = True

        if received_any or self._packets_sent != packets_sent_before or tick_slept:
            self._tick_sleep_hint = 0.0
        else:
            self._tick_sleep_hint = self._config.tunnel_tick_sleep
        return True

    def _check_serial_window_block(self):
        if self._serial_window_negotiation():
            if self._send_window.unacked_count > 0:
                return {'reason': 'serial_window', 'silent': True}
        return None

    def _check_send_window_full(self, allow_window_full, keepalive_only):
        if self._send_window.can_send:
            return None
        if allow_window_full:
            return None
        return {
            'reason': 'window_full',
            'keepalive_only': keepalive_only,
            'unacked': self._send_window.unacked_count,
            'max_in_flight': self._send_window._max_in_flight,
        }

    def _check_send_window_distance(self, now, pacer_cap, keepalive_only):
        exceeded, distance_info = self._send_window.distance_exceeded(
            max_window=self.MAX_WINDOW
        )
        if not exceeded:
            self._maybe_unfreeze_pacer_feedback(now, reason='distance_clear')
            return None
        distance_details = self._send_window.distance_details(now=now)
        self._update_pacer_feedback_freeze(
            now,
            distance_info,
            distance_details,
            keepalive_only,
        )
        return {
            'reason': 'window_distance',
            'keepalive_only': keepalive_only,
            'distance_info': distance_info,
            'distance_details': distance_details,
            'pacer_cap': pacer_cap,
        }

    def _check_send_rate_limit(self, now, keepalive_only):
        if self._send_limiter is None:
            return None
        if self._send_limiter.can_send(now=now):
            return None
        return {
            'reason': 'rate_limit',
            'keepalive_only': keepalive_only,
            'rate': self._config.tunnel_send_rate,
            'burst': self._config.tunnel_send_burst,
        }

    def _check_send_pacer(self, keepalive_only, cap, now=None, pacer_state=None):
        if not self._pacer.enabled:
            return None
        if cap is None:
            cap = self._pacer_cap()
        unacked_count = self._send_window.unacked_count
        if pacer_state is None:
            pacer_state = self._pacer.target_state(
                cap,
                srtt_ms=self._rtt.srtt_ms,
                now=now,
            )
        fields = self._pacer.state_fields(
            unacked_count,
            cap,
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
            now=now,
            state=pacer_state,
        )
        self._maybe_log_pacer_target_change(
            cap,
            reason='gate_check',
            fields=fields,
        )
        if keepalive_only:
            return None
        unacked, inflight = self._pacer_inflight_counts()
        if inflight is None:
            inflight = unacked
        if inflight < pacer_state.target_inflight:
            return None
        return {
            'reason': 'pacer',
            'keepalive_only': keepalive_only,
            'unacked': unacked,
            'inflight': inflight,
            'cap': cap,
        }

    def _pacer_inflight_counts(self):
        unacked = self._send_window.unacked_count
        distance_info = self._send_window.distance_info()
        if distance_info is None:
            return unacked, None
        distance = distance_info[0]
        if distance < unacked:
            distance = unacked
        return unacked, distance

    def _should_freeze_pacer_feedback(self, distance_info, details):
        if not self._pacer.enabled:
            return False
        if details is None:
            return False
        if not details.get('missing_in_unacked'):
            return False
        missing_age = details.get('missing_age')
        if missing_age is None:
            return False
        min_age = self._rtt.rto_sec * self._fast_retransmit_min_age_ratio
        if missing_age < min_age:
            return False
        (distance, max_in_flight, effective_cap, unacked,
         _distance_limit, _last_cum_ack, _next_seq) = distance_info
        buffered = distance - unacked
        cap = effective_cap
        if cap is None:
            cap = max_in_flight
        if cap is None:
            cap = self._send_window._max_in_flight
        if cap < 1:
            cap = 1
        low_unacked = max(2, int(cap * 0.25))
        high_buffered = max(4, int(cap * 0.5))
        if unacked > low_unacked:
            return False
        if buffered < high_buffered:
            return False
        return True

    def _update_pacer_feedback_freeze(self, now, distance_info, details,
                                      keepalive_only):
        if not self._pacer.enabled:
            return
        if keepalive_only:
            return
        should_freeze = self._should_freeze_pacer_feedback(
            distance_info,
            details,
        )
        if should_freeze:
            if self._pacer.freeze_feedback(now, reason='sack_stall'):
                self._log_pacer_feedback_freeze(
                    action='freeze',
                    reason='sack_stall',
                    distance_info=distance_info,
                    details=details,
                )
            return
        if self._pacer.unfreeze_feedback(now):
            self._log_pacer_feedback_freeze(
                action='unfreeze',
                reason='stall_clear',
                distance_info=distance_info,
                details=details,
            )

    def _maybe_unfreeze_pacer_feedback(self, now, reason):
        if not self._pacer.enabled:
            return
        if self._pacer.unfreeze_feedback(now):
            self._log_pacer_feedback_freeze(
                action='unfreeze',
                reason=reason,
                distance_info=None,
                details=None,
            )

    def _log_pacer_feedback_freeze(self, action, reason, distance_info,
                                   details):
        def build_fields():
            fields = {
                'side': 'alice',
                'action': action,
                'reason': reason,
            }
            if distance_info is not None:
                (distance, max_in_flight, effective_cap, unacked,
                 distance_limit, last_cum_ack, next_seq) = distance_info
                buffered = distance - unacked
                fields.update({
                    'distance': distance,
                    'distance_limit': distance_limit,
                    'buffered': buffered,
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
                    'effective_cap': effective_cap,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                })
            if details is not None:
                fields.update({
                    'missing_in_unacked': details.get('missing_in_unacked'),
                    'missing_age': details.get('missing_age'),
                    'ack_miss_count': details.get('ack_miss_count'),
                    'ack_miss_last_age': details.get('ack_miss_last_age'),
                })
            return fields
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.pacer_feedback_freeze',
            'Pacer feedback freeze update',
            build_fields,
        )

    def _log_send_blocked(self, decision, now):
        reason = decision.get('reason')
        keepalive_only = decision.get('keepalive_only')
        if reason == 'window_full':
            unacked = decision.get('unacked', 0)
            max_in_flight = decision.get('max_in_flight')
            if unacked == 0:
                log_event(
                    self._logger,
                    logging.ERROR,
                    'tunnel.send_window_inconsistent',
                    'Send window full but no unacked packets',
                    lambda: {
                        'unacked': unacked,
                        'max_in_flight': max_in_flight,
                        'side': 'alice',
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
                        'max_in_flight': max_in_flight,
                        'side': 'alice',
                    },
                )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window full',
                lambda: {
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
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
                unacked=unacked,
            )
            return

        if reason == 'window_distance':
            distance_info = decision.get('distance_info')
            pacer_cap = decision.get('pacer_cap')
            distance_details = decision.get('distance_details')
            if distance_details is None:
                distance_details = self._send_window.distance_details(now=now)
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
                fields.update(distance_details)
                return fields
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_window_distance',
                'Send window distance exceeded',
                build_distance_fields,
            )
            def build_blocked_fields():
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
                    'reason': 'window_distance',
                }
                if pacer_cap is not None:
                    fields['pacer_cap'] = pacer_cap
                return fields
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send window distance exceeded',
                build_blocked_fields,
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
            return

        if reason == 'rate_limit':
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send rate limited',
                lambda: {
                    'side': 'alice',
                    'rate': decision.get('rate'),
                    'burst': decision.get('burst'),
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
            return

        if reason == 'pacer':
            unacked = decision.get('unacked')
            inflight = decision.get('inflight')
            cap = decision.get('cap')
            self._log_pacer_state(
                cap,
                unacked,
                action='blocked',
                inflight_count=inflight,
                now=now,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.send_blocked',
                'Send pacing blocked',
                lambda: {
                    'side': 'alice',
                    'reason': 'pacer',
                    'unacked': unacked,
                    'inflight': inflight,
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
                    'inflight': inflight,
                    'cap': cap,
                },
            )
            return

    def _can_send_new(self, now=None, keepalive_only=False, allow_window_full=False):
        """Check if we can send a new packet."""
        if now is None:
            now = time_provider.now()
        decision = self._check_serial_window_block()
        if decision is not None:
            return False
        decision = self._check_send_window_full(allow_window_full, keepalive_only)
        if decision is not None:
            self._log_send_blocked(decision, now)
            return False
        pacer_cap = None
        pacer_gate_cap = None
        pacer_state = None
        if self._pacer.enabled:
            pacer_gate_cap = self._pacer_cap()
            pacer_state = self._pacer.target_state(
                pacer_gate_cap,
                srtt_ms=self._rtt.srtt_ms,
                now=now,
            )
            pacer_cap = min(
                self._send_window._max_in_flight,
                pacer_state.target_inflight,
            )
        decision = self._check_send_window_distance(
            now,
            pacer_cap,
            keepalive_only,
        )
        if decision is not None:
            self._log_send_blocked(decision, now)
            return False
        decision = self._check_send_rate_limit(now, keepalive_only)
        if decision is not None:
            self._log_send_blocked(decision, now)
            return False
        decision = self._check_send_pacer(
            keepalive_only,
            pacer_gate_cap,
            now=now,
            pacer_state=pacer_state,
        )
        if decision is not None:
            self._log_send_blocked(decision, now)
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
        return self._send_window.sack_progress_ready()

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
        cap_override = None
        if self._pacer.enabled:
            pacer_cap = self._pacer_cap()
            cap_override = self._pacer.target_inflight(
                pacer_cap,
                srtt_ms=self._rtt.srtt_ms,
                now=now,
            )
        exceeded, distance_info = self._send_window.distance_exceeded(
            cap_override=cap_override,
            max_window=self.MAX_WINDOW,
        )
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
        count = self._fast_retransmit_counts.get(seq, 0)
        min_age = self._rtt.rto_sec * self._fast_retransmit_min_age_ratio
        min_rto_sec = self._config.protocol_min_rto_ms / 1000.0
        if min_age > min_rto_sec:
            min_age = min_rto_sec
        if count >= self._fast_retransmit_max_per_seq:
            # Back off fast retransmits after the per-seq cap to avoid churn.
            min_age *= (count - self._fast_retransmit_max_per_seq + 2)
        if missing_age < min_age:
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

    def _reserve_transport_permit(self, now, has_data_pending=None):
        if has_data_pending is None:
            has_data_pending = self._channel_manager.has_pending_data(
                mode='data'
            )
        self._transport.notify_send_pending(has_data_pending)
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

    def _maybe_log_pacer_target_change(self, cap, reason=None, fields=None,
                                       now=None):
        if not self._pacer.enabled:
            return
        if fields is None:
            fields = self._pacer.state_fields(
                self._send_window.unacked_count,
                cap,
                rate_limit=self._config.tunnel_send_rate,
                srtt_ms=self._rtt.srtt_ms,
                now=now,
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
            logging.DEBUG,
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
            logging.DEBUG,
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
        if baseline_target >= base_target:
            return
        self._log_pacer_adjust(prev_target, 'feedback')

    def _note_pacer_blocked(self, reason, now, unacked=None):
        if reason in self._pacer_blocked_counts:
            self._pacer_blocked_counts[reason] += 1
        if not self._pacer.enabled:
            return
        cap = self._pacer_cap()
        srtt_ms = self._rtt.srtt_ms
        prev_target = self._pacer.target_inflight(
            cap,
            srtt_ms=srtt_ms,
            now=now,
        )
        adjusted = self._pacer.on_blocked(
            reason,
            now,
            cap,
            srtt_ms=srtt_ms,
            unacked_count=unacked,
        )
        if not adjusted:
            return
        target = self._pacer.target_inflight(
            cap,
            srtt_ms=srtt_ms,
            now=now,
        )
        if target >= prev_target:
            return
        self._log_pacer_adjust(prev_target, 'blocked', block_reason=reason)

    def _log_pacer_state(self, cap, unacked_count, action=None,
                         inflight_count=None, now=None):
        if not self._pacer.enabled:
            return
        fields = self._pacer.state_fields(
            unacked_count,
            cap,
            rate_limit=self._config.tunnel_send_rate,
            srtt_ms=self._rtt.srtt_ms,
            now=now,
        )
        if inflight_count is not None and inflight_count != unacked_count:
            fields['inflight_count'] = inflight_count
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
            now=now,
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
        ack_silence = self._send_window.ack_silence(now=now)
        if ack_silence is not None:
            fields['ack_silence'] = round(ack_silence, 6)
        ack_progress_silence = self._send_window.ack_progress_silence(now=now)
        if ack_progress_silence is not None:
            fields['ack_progress_silence'] = round(
                ack_progress_silence, 6
            )
        exceeded, distance_info = self._send_window.distance_exceeded(
            max_window=self.MAX_WINDOW
        )
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
        flags &= ~(FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS)
        if segments:
            flags |= FLAG_HAS_SEGMENTS
        else:
            flags |= FLAG_KEEPALIVE
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
            self._transport.send(packet_data, permit)
        except Exception as exc:
            self._transport.release_send(permit)
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.packet_send_failed',
                'Packet send failed',
                lambda: {
                    'seq': packet.seq,
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'flags': packet.flags,
                    'seg_count': len(packet.segments),
                    'bytes': len(packet_data),
                    'context': 'new',
                    'side': 'alice',
                    'error': str(exc),
                },
                exc_info=True,
            )
            return
        self._send_window.send(
            segments,
            flags=packet.flags,
            encrypted_body=encrypted_body,
            now=now,
        )
        self._advance_poll_pacing(now)
        if self._pacer.enabled:
            cap = self._pacer_cap()
            unacked, inflight = self._pacer_inflight_counts()
            self._log_pacer_state(
                cap,
                unacked,
                action='send',
                inflight_count=inflight,
                now=now,
            )

        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
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

        permit = self._reserve_transport_permit(
            now,
        )
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
            self._transport.send(packet_data, permit)
        except Exception as exc:
            self._transport.release_send(permit)
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.packet_send_failed',
                'Packet send failed',
                lambda: {
                    'seq': packet.seq,
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'flags': packet.flags,
                    'seg_count': len(packet.segments),
                    'bytes': len(packet_data),
                    'context': 'retransmit',
                    'reason': reason,
                    'side': 'alice',
                    'error': str(exc),
                },
                exc_info=True,
            )
            return False
        self._send_window.mark_retransmit(seq, now=now)
        if self._pacer.enabled:
            self._pacer.on_retransmit(now)
        self._advance_poll_pacing(now)

        self._consume_retransmit_budget()
        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        def build_fields():
            fields = {
                'seq': seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(segments),
                'bytes': len(packet_data),
                'side': 'alice',
                'send_packet_mtu': self._send_packet_mtu,
                'recv_packet_mtu': self._recv_packet_mtu,
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
            return (False, None)

        self._bytes_received += len(data)
        self._last_recv_time = now

        if packet.flags & (FLAG_SYN | FLAG_ACK):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.handshake_late_packet',
                'Ignored stale handshake packet',
                lambda: {
                    'flags': packet.flags,
                    'side': 'alice',
                },
            )
            return (True, None)

        response_kind = self._content_flag_label(packet.flags)
        if response_kind == 'has_segments':
            self._got_data = True
        elif response_kind not in ('keepalive',):
            response_kind = None

        prev_unacked = self._send_window.unacked_count
        rtt_samples, acked_count, data_acked_count = self._process_incoming_packet(
            packet, now=now, packet_size=packet_size
        )
        self._transport.notify_recv_window_sack(self._recv_window.sack)
        new_unacked = self._send_window.unacked_count
        if rtt_samples or acked_count > 0:
            self._ack_progressed = True
        for sample in rtt_samples:
            self._rtt.add_sample(sample)
        if self._pacer.enabled and data_acked_count > 0:
            self._pacer.on_ack(
                data_acked_count,
                now,
                srtt_ms=self._rtt.srtt_ms,
                sack=packet.sack,
            )
            self._maybe_log_pacer_target_change(
                self._pacer_cap(),
                reason='ack',
                now=now,
            )
        if (self._pacer.enabled and self._pacer.feedback_frozen and
                acked_count > 0):
            exceeded, _ = self._send_window.distance_exceeded(
                max_window=self.MAX_WINDOW
            )
            if not exceeded:
                if self._pacer.unfreeze_feedback(now):
                    self._log_pacer_feedback_freeze(
                        action='unfreeze',
                        reason='ack_progress',
                        distance_info=None,
                        details=None,
                    )
        return (True, response_kind)

    def _maybe_request_window(self, now):
        """Request a larger window if conditions allow."""
        if self._window_final:
            return
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

            sleep_hint = self._tick_sleep_hint
            if sleep_hint > 0:
                time_provider.sleep(sleep_hint)

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
            sleep_hint = self._tick_sleep_hint
            if sleep_hint > 0:
                time_provider.sleep(sleep_hint)

    def close(self):
        """Close the tunnel and transport."""
        super(AliceTunnel, self).close()
        try:
            self._transport.close()
        except Exception:
            pass
