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
from ..reliability import (
    AdaptivePacer,
    compute_poll_pacing_interval,
    FastRetransmitController,
    PacerGateController,
    PacerLoggingHelper,
    RttEstimator,
)
from ..reliability.send_window import SendWindowError
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
)
from ..logging_util import log_event
from .. import time_provider


class AliceTunnel(BaseTunnel):
    """
    Client-side tunnel.

    Alice initiates the handshake and drives the tunnel using tick().
    She uses RTT-based retransmission timing and supports pipelining.
    """

    _NO_RESPONSE_TIMEOUT_MULTIPLIER = 60.0
    _WINDOW_GROWTH_INTERVAL_FACTOR = 10.0

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
        self._fast_retransmit = FastRetransmitController(
            self._send_window,
            self._rtt,
            enabled=config.tunnel_fast_retransmit_enabled,
            min_age_ratio=config.tunnel_fast_retransmit_min_age_ratio,
            max_per_seq=config.tunnel_fast_retransmit_max_per_seq,
            min_rto_ms=config.protocol_min_rto_ms,
        )
        self._tick_epoch = 0
        self._backoff_epoch = None

        # Timing
        self._last_send_time = 0
        self._last_recv_time = 0

        # Timeout detection: seconds without any response
        self._no_response_timeout = self._derive_no_response_timeout()

        # Track if Bob sent real data since the last poll we sent.
        self._got_data = False
        # Track keepalive-only responses (legacy "pong" terminology)
        self._last_was_pong_only = False
        self._pong_grace_polls = self._proposed_window * 2
        self._pong_grace_remaining = self._pong_grace_polls
        # Track if we have real data packets awaiting ACKs (not just keepalives)
        self._has_pending_data_acks = False
        # Window growth state (Alice only)
        self._window_growth_enabled = config.tunnel_window_growth_enabled
        self._window_growth_step = config.tunnel_window_growth_step
        self._last_window_request_time = 0
        self._ack_progressed = False
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
        self._pacer_logger = PacerLoggingHelper(
            config.tunnel_pacer_summary_interval
        )
        self._pacer_gate = PacerGateController()

        # Poll pacing (Alice only)
        self._poll_pacing_enabled = config.tunnel_poll_pacing_enabled
        self._poll_min_interval = config.tunnel_poll_min_interval
        self._poll_rtt_ratio = config.tunnel_poll_rtt_ratio
        self._next_poll_time = 0.0
        self._poll_pace_interval = None
        self._poll_pace_sleep_max = 0.01
        self._tick_sleep_hint = 0.0

        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.init',
                'Tunnel init',
                lambda: {
                    'side': 'alice',
                    'keepalive_interval': self._keepalive_interval,
                    'poll_min_interval': self._poll_min_interval,
                    'poll_max_interval': self._keepalive_interval,
                    'poll_rtt_ratio': self._poll_rtt_ratio,
                    'pong_grace_polls': self._pong_grace_polls,
                    'proposed_window': self._proposed_window,
                    'no_response_timeout': self._no_response_timeout,
                },
            )

        # Enable module loader for handling Bob's module requests.
        self.enable_module_loader()

    def _derive_no_response_timeout(self):
        timeout = float(self._keepalive_interval) * self._NO_RESPONSE_TIMEOUT_MULTIPLIER
        if timeout <= 0:
            raise TunnelError('Derived no-response timeout invalid')
        return timeout

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
            if self._logger.isEnabledFor(logging.DEBUG):
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
                if self._logger.isEnabledFor(logging.WARNING):
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
                            if self._logger.isEnabledFor(logging.DEBUG):
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

            if self._logger.isEnabledFor(logging.INFO):
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
            if self._logger.isEnabledFor(logging.WARNING):
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
        if self._logger.isEnabledFor(logging.INFO):
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
        if self._logger.isEnabledFor(logging.INFO):
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
        self.check_bg_error()
        if self._state != TunnelState.CONNECTED:
            return False

        try:
            now = time_provider.now()
            self._tick_epoch += 1
            self._retransmit_budget = self._retransmit_cap
            packets_sent_before = self._packets_sent
            serial_window = self._serial_window_negotiation()
            # 1. Receive all available responses
            received_any, received_valid, last_resp_kind = (
                self._drain_transport_responses(now)
            )
            self._update_response_state(received_valid, last_resp_kind)

            if not self._check_no_response_timeout(now):
                return False

            if self._fast_retransmit.enabled:
                self._fast_retransmit.prune()

            # 2. Check for retransmits
            # Avoid RTO retransmits while ACKs are still advancing.
            ack_silence = self._send_window.ack_silence(now=now)
            if ack_silence is None or ack_silence >= self._rtt.rto_sec:
                retransmits = self._send_window.get_retransmits(
                    self._rtt.rto_sec, now=now
                )
                if self._logger.isEnabledFor(logging.DEBUG):
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
                if self._logger.isEnabledFor(logging.DEBUG):
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
            tick_slept = False
            pacing_blocked, sent_any = self._send_pending_or_poll(
                now,
                serial_window,
                packets_sent_before,
            )

            # 4. Opportunistically grow window after ACK progress or retry negotiation
            if self._window_growth_enabled:
                self._maybe_request_window(now)

            self._maybe_log_pacer_summary(now)

            paced_sleep = False
            if pacing_blocked and not sent_any:
                paced_sleep = self._sleep_for_poll_pacing(time_provider.now())
                if paced_sleep:
                    tick_slept = True

            if (not paced_sleep and not received_any and not sent_any and
                    not self._channel_manager.pending_send_event.is_set() and
                    not self._got_data and not self._has_pending_data_acks):
                idle_sleep = max(self._config.tunnel_tick_sleep, 0.01)
                time_provider.sleep(idle_sleep)
                tick_slept = True

            if received_any or sent_any or tick_slept:
                self._tick_sleep_hint = 0.0
            else:
                self._tick_sleep_hint = self._config.tunnel_tick_sleep
            return True
        except SendWindowError as exc:
            if self._logger.isEnabledFor(logging.ERROR):
                log_event(
                    self._logger,
                    logging.ERROR,
                    'tunnel.send_window_inconsistent',
                    'Send window inconsistent',
                    lambda: {
                        'seq': exc.seq,
                        'context': exc.context,
                        'side': 'alice',
                        'error': str(exc),
                    },
                )
            self.close()
            raise

    def _drain_transport_responses(self, now):
        """Receive all available responses from the transport."""
        received_any = False
        received_valid = False
        last_resp_kind = None
        while True:
            corr_id, data = self._transport.recv(
                timeout=self._config.non_blocking_poll_timeout
            )
            if corr_id is None:
                break
            valid, resp_kind = self._handle_response(corr_id, data, now)
            if valid:
                received_valid = True
                if resp_kind is not None:
                    last_resp_kind = resp_kind
            received_any = True

        # If pending is high and we didn't receive anything, wait briefly.
        if not received_any and hasattr(self._transport, 'pending_count'):
            pending = self._transport.pending_count()
            cap = getattr(self._transport, 'max_in_flight', None)
            if cap is None:
                cap = self._send_window._max_in_flight
            threshold = int(cap * 0.75)
            if pending >= threshold:
                corr_id, data = self._transport.recv(timeout=0.05)
                if corr_id is not None:
                    valid, resp_kind = self._handle_response(corr_id, data, now)
                    if valid:
                        received_valid = True
                        if resp_kind is not None:
                            last_resp_kind = resp_kind
                    received_any = True

        return received_any, received_valid, last_resp_kind

    def _update_response_state(self, received_valid, last_resp_kind):
        if not received_valid:
            return
        # Clear pending data flag if all data has been acked.
        if self._send_window.data_unacked_count() == 0:
            self._has_pending_data_acks = False
        if last_resp_kind is not None:
            self._last_was_pong_only = (last_resp_kind == 'keepalive')
            if last_resp_kind == 'has_segments':
                self._pong_grace_remaining = self._pong_grace_polls

    def _check_no_response_timeout(self, now):
        """Return False if a no-response timeout closes the tunnel."""
        if not self._last_recv_time:
            return True
        silence = now - self._last_recv_time
        if silence < self._no_response_timeout:
            return True
        self._set_state(TunnelState.CLOSED)
        if self._logger.isEnabledFor(logging.ERROR):
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

    def _pending_mode_set(self, mode, pending_event, control_send_event,
                          data_send_event):
        if mode == 'control':
            return control_send_event.is_set()
        if mode == 'data':
            return data_send_event.is_set()
        return pending_event.is_set()

    def _try_send_segments(self, now, send_payload_limit, control_only,
                           has_data_pending=None, mark_pending_acks=False,
                           permit=None, keep_permit_on_empty=False):
        """Collect segments and send if available."""
        if permit is None:
            permit = self._reserve_transport_permit(
                now,
                has_data_pending=has_data_pending,
            )
        if permit is None:
            return False, None
        payload_cap = self._transport.payload_cap_for_send(permit)
        segments = self._collect_segments(
            send_payload_limit,
            control_only=control_only,
            payload_cap=payload_cap,
        )
        if not segments:
            if not keep_permit_on_empty:
                self._transport.release_send(permit)
                return False, None
            return False, permit
        if mark_pending_acks:
            self._has_pending_data_acks = True
        self._send_new_packet(segments, now, permit=permit)
        return True, None

    def _send_keepalive_or_break(self, now, permit, window_full,
                                 consume_pong_grace):
        """Send a keepalive or release the permit on failure."""
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
                return False
        self._send_new_packet([], now, flags=FLAG_KEEPALIVE, permit=permit)
        if consume_pong_grace and self._pong_grace_remaining > 0:
            self._pong_grace_remaining -= 1
        return True

    def _send_pending_or_poll(self, now, serial_window, packets_sent_before):
        """Send pending segments or keepalive polls."""
        send_payload_limit = self._payload_mtu_from_packet(
            self._send_packet_mtu
        )
        pacing_blocked = False
        sent_any = (self._packets_sent != packets_sent_before)
        pending_event = self._channel_manager.pending_send_event
        control_send_event = self._channel_manager.control.send_event
        data_send_event = self._channel_manager.data_send_event

        pending_mode = 'control' if serial_window else 'control_or_data'
        control_only = serial_window
        break_on_empty = not serial_window
        while True:
            if self._pending_mode_set(
                    pending_mode,
                    pending_event,
                    control_send_event,
                    data_send_event):
                if not self._can_send_new(
                        now=now,
                        keepalive_only=False):
                    break
                if not self._poll_pacing_allows_send(now):
                    pacing_blocked = True
                    break
                sent, permit = self._try_send_segments(
                    now,
                    send_payload_limit,
                    control_only,
                    has_data_pending=data_send_event.is_set(),
                    mark_pending_acks=True,
                    keep_permit_on_empty=True,
                )
                if sent:
                    sent_any = True
                    continue
                if permit is None:
                    break
                self._transport.release_send(permit)
                if break_on_empty:
                    break

            should_poll, keepalive_due, consume_pong_grace = self._poll_decision(now)
            if not should_poll:
                break
            if self._pending_mode_set(
                    pending_mode,
                    pending_event,
                    control_send_event,
                    data_send_event):
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
            sent, permit = self._try_send_segments(
                now,
                send_payload_limit,
                control_only,
                mark_pending_acks=False,
                keep_permit_on_empty=True,
            )
            if sent:
                sent_any = True
                continue
            if permit is None:
                break
            if break_on_empty and self._pending_mode_set(
                    pending_mode,
                    pending_event,
                    control_send_event,
                    data_send_event):
                self._transport.release_send(permit)
                break
            if not self._send_keepalive_or_break(
                    now,
                    permit,
                    window_full,
                    consume_pong_grace):
                break
            sent_any = True

        return pacing_blocked, sent_any

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

    def _pacer_inflight_counts(self):
        unacked = self._send_window.unacked_count
        distance_info = self._send_window.distance_info()
        if distance_info is None:
            return unacked, None
        distance = distance_info[0]
        if distance < unacked:
            distance = unacked
        return unacked, distance

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
        if self._logger.isEnabledFor(logging.DEBUG):
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
                if self._logger.isEnabledFor(logging.ERROR):
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
                if self._logger.isEnabledFor(logging.DEBUG):
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
            if self._logger.isEnabledFor(logging.DEBUG):
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
            if self._logger.isEnabledFor(logging.DEBUG):
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
            if self._logger.isEnabledFor(logging.DEBUG):
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

        if reason == 'pacer':
            unacked = decision.get('unacked')
            inflight = decision.get('inflight')
            cap = decision.get('cap')
            self._log_pacer_state(
                cap,
                unacked,
                action='blocked',
                inflight_count=inflight,
            )
            if self._logger.isEnabledFor(logging.DEBUG):
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
        pacer_target = None
        if self._pacer.enabled:
            pacer_gate_cap = self._pacer_cap()
            if self._logger.isEnabledFor(logging.DEBUG):
                pacer_target = self._pacer.target_inflight(
                    pacer_gate_cap,
                    srtt_ms=self._rtt.srtt_ms,
                )
                pacer_cap = min(self._send_window._max_in_flight, pacer_target)
        decision = self._pacer_gate.check_send(
            send_window=self._send_window,
            pacer=self._pacer,
            now=now,
            srtt_ms=self._rtt.srtt_ms,
            rto_sec=self._rtt.rto_sec,
            min_age_ratio=self._fast_retransmit.min_age_ratio,
            keepalive_only=keepalive_only,
            pacer_cap=pacer_cap,
            max_window=self.MAX_WINDOW,
            pacer_gate_cap=pacer_gate_cap,
            check_pacer=False,
            pacer_target=pacer_target,
        )
        freeze_action = decision.get('freeze_action')
        if freeze_action is not None:
            freeze_details = decision.get('freeze_details') or {}
            self._log_pacer_feedback_freeze(
                action=freeze_action,
                reason=decision.get('freeze_reason'),
                distance_info=freeze_details.get('distance_info'),
                details=freeze_details.get('details'),
            )
        if not decision.get('can_send'):
            blocked = dict(decision.get('block_details') or {})
            blocked['reason'] = decision.get('block_reason')
            self._log_send_blocked(blocked, now)
            return False
        if self._pacer.enabled:
            self._maybe_log_pacer_target_change(
                pacer_gate_cap,
                reason='gate_check',
            )
        decision = self._pacer_gate.check_send(
            send_window=self._send_window,
            pacer=self._pacer,
            now=now,
            srtt_ms=self._rtt.srtt_ms,
            rto_sec=self._rtt.rto_sec,
            min_age_ratio=self._fast_retransmit.min_age_ratio,
            keepalive_only=keepalive_only,
            pacer_cap=pacer_cap,
            max_window=self.MAX_WINDOW,
            pacer_gate_cap=pacer_gate_cap,
            check_distance=False,
            pacer_target=pacer_target,
        )
        if not decision.get('can_send'):
            blocked = dict(decision.get('block_details') or {})
            blocked['reason'] = decision.get('block_reason')
            self._log_send_blocked(blocked, now)
            return False
        return True

    def _can_send_retransmit(self, now=None):
        """Check if we can send a retransmit packet."""
        if now is None:
            now = time_provider.now()
        if self._retransmit_budget is not None and self._retransmit_budget <= 0:
            if self._logger.isEnabledFor(logging.DEBUG):
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
        return True

    def _maybe_fast_retransmit(self, now, ack_silence):
        if not self._fast_retransmit.enabled:
            return False
        cap_override = None
        if self._pacer.enabled:
            pacer_cap = self._pacer_cap()
            cap_override = self._pacer.target_inflight(
                pacer_cap,
                srtt_ms=self._rtt.srtt_ms,
            )
        candidate = self._fast_retransmit.select_candidate(
            now,
            ack_silence,
            max_window=self.MAX_WINDOW,
            cap_override=cap_override,
        )
        if candidate is None:
            return False
        if not self._can_send_retransmit(now=now):
            return False
        (seq, segments, flags, encrypted_body, _send_time) = candidate
        sent = self._send_retransmit(
            seq,
            segments,
            flags,
            encrypted_body,
            now,
            reason='fast_retransmit',
        )
        if sent:
            self._fast_retransmit.note_sent(seq)
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
            self._log_transport_blocked(now)
            return None
        return permit

    def _log_transport_blocked(self, now):
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
        if self._logger.isEnabledFor(logging.DEBUG):
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
            now=now,
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
        interval, target_inflight = compute_poll_pacing_interval(
            srtt_ms=srtt_ms,
            keepalive_interval=self._keepalive_interval,
            rtt_floor_ms=self._config.tunnel_pace_rtt_floor_ms,
            poll_rtt_ratio=self._poll_rtt_ratio,
            min_interval=self._poll_min_interval,
            target_inflight=target_inflight,
        )
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
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
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
        if (not self._logger.isEnabledFor(logging.DEBUG)
                and self._pacer_logger.summary_interval <= 0):
            return
        decision = self._pacer_logger.maybe_target_event(
            self._pacer,
            self._send_window.unacked_count,
            cap,
            srtt_ms=self._rtt.srtt_ms,
            side='alice',
            reason=reason,
        )
        if decision is None:
            return
        if decision.get('feedback_adjust'):
            adjust_fields = self._pacer_logger.adjust_fields(
                self._pacer,
                self._send_window.unacked_count,
                self._pacer_cap(),
                srtt_ms=self._rtt.srtt_ms,
                side='alice',
                prev_target=decision.get('prev_target'),
                reason='feedback',
            )
            if adjust_fields is not None:
                if self._logger.isEnabledFor(logging.DEBUG):
                    log_event(
                        self._logger,
                        logging.DEBUG,
                        'tunnel.pacer_adjust',
                        'Pacer target decreased',
                        lambda: adjust_fields,
                    )
        fields = decision.get('fields')
        target = decision.get('target')
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.pacer_target',
                'Pacer target adjusted to %s' % target,
                lambda: fields,
            )

    def _log_pacer_adjust(self, prev_target, reason, block_reason=None):
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        fields = self._pacer_logger.adjust_fields(
            self._pacer,
            self._send_window.unacked_count,
            self._pacer_cap(),
            srtt_ms=self._rtt.srtt_ms,
            side='alice',
            prev_target=prev_target,
            reason=reason,
            block_reason=block_reason,
        )
        if fields is None:
            return
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.pacer_adjust',
                'Pacer target decreased',
                lambda: fields,
            )

    def _note_pacer_blocked(self, reason, now, unacked=None):
        self._pacer_logger.note_blocked(reason)
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

    def _log_pacer_state(self, cap, unacked_count, action=None,
                         inflight_count=None):
        if (not self._logger.isEnabledFor(logging.DEBUG)
                and self._pacer_logger.summary_interval <= 0):
            return
        fields = self._pacer_logger.state_fields(
            self._pacer,
            unacked_count,
            cap,
            srtt_ms=self._rtt.srtt_ms,
            side='alice',
            action=action,
            inflight_count=inflight_count,
        )
        if fields is None:
            return
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.pacer_state',
                'Pacer state',
                lambda: fields,
            )

    def _maybe_log_pacer_summary(self, now):
        action = self._pacer_logger.summary_action(now)
        if action in ('disabled', 'skip'):
            return
        stats_snapshot = None
        if self._stats_enabled and action in ('init', 'log'):
            try:
                stats_snapshot = self._reliability_stats.snapshot()
            except Exception:
                stats_snapshot = None
        fields = self._pacer_logger.maybe_summary_fields(
            now,
            self._pacer,
            self._send_window,
            self.MAX_WINDOW,
            self._state,
            self._packets_sent,
            self._packets_received,
            self._transport,
            self._pacer_cap(),
            self._rtt.srtt_ms,
            self._stats_enabled,
            stats_snapshot,
        )
        if fields is None:
            return
        if self._logger.isEnabledFor(logging.INFO):
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.pacer_summary',
                'Pacer summary',
                lambda: fields,
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
        flags &= ~(FLAG_KEEPALIVE | FLAG_HAS_SEGMENTS)
        if segments:
            flags |= FLAG_HAS_SEGMENTS
        else:
            flags |= FLAG_KEEPALIVE
        packet, seq = self._build_packet(flags=flags, segments=segments)
        encrypted_body, packet_data = self._encode_packet_for_send(packet)

        if permit is None:
            permit = self._reserve_transport_permit(now)
            if permit is None:
                return

        try:
            self._transport.send(packet_data, permit)
        except Exception as exc:
            self._transport.release_send(permit)
            if self._logger.isEnabledFor(logging.WARNING):
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
            )

        self._last_send_time = now
        self._packets_sent += 1
        self._bytes_sent += len(packet_data)
        if self._logger.isEnabledFor(logging.DEBUG):
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
            prev_age = now - prev_send_time
            prev_age = round(prev_age, 6)
        try:
            self._transport.send(packet_data, permit)
        except Exception as exc:
            self._transport.release_send(permit)
            if self._logger.isEnabledFor(logging.WARNING):
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
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.retransmit',
                'Retransmitting packet',
                build_fields,
            )
        if self._logger.isEnabledFor(logging.DEBUG):
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

    def _handle_response(self, corr_id, data, now):
        """Handle a transport response."""
        packet, packet_size = self._decode_packet(data, return_size=True)
        if packet is None:
            if self._logger.isEnabledFor(logging.DEBUG):
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'tunnel.response_decode_failed',
                    'Transport response decode failed',
                    lambda: {
                        'corr_id': corr_id,
                        'bytes': len(data),
                        'side': 'alice',
                    },
                )
            return (False, None)

        self._bytes_received += len(data)
        self._last_recv_time = now
        content_flag = self._content_flag_label(packet.flags)
        if self._logger.isEnabledFor(logging.DEBUG):
            log_event(
                self._logger,
                logging.DEBUG,
                'tunnel.response_decode',
                'Transport response decoded',
                lambda: {
                    'corr_id': corr_id,
                    'seq': packet.seq,
                    'ack': packet.ack,
                    'sack': packet.sack,
                    'flags': packet.flags,
                    'content_flag': content_flag,
                    'seg_count': len(packet.segments),
                    'bytes': packet_size,
                    'side': 'alice',
                },
            )

        if packet.flags & (FLAG_SYN | FLAG_ACK):
            if self._logger.isEnabledFor(logging.DEBUG):
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

        response_kind = content_flag
        if response_kind == 'has_segments':
            self._got_data = True
        elif response_kind not in ('keepalive',):
            response_kind = None

        rtt_samples, acked_count, data_acked_count = self._process_incoming_packet(
            packet, now=now, packet_size=packet_size
        )
        self._transport.notify_recv_window_sack(self._recv_window.sack)
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
            self._maybe_log_pacer_target_change(self._pacer_cap(), reason='ack')
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

    def _window_growth_interval(self):
        base_interval = None
        srtt_ms = self._rtt.srtt_ms
        if srtt_ms is not None and srtt_ms > 0:
            base_interval = srtt_ms / 1000.0
        poll_interval = None
        if self._poll_pacing_enabled:
            poll_interval, _ = self._poll_pacing_interval()
        if poll_interval is not None and poll_interval > 0:
            if base_interval is None or poll_interval > base_interval:
                base_interval = poll_interval
        if base_interval is None or base_interval <= 0:
            base_interval = self._keepalive_interval
        interval = base_interval * self._WINDOW_GROWTH_INTERVAL_FACTOR
        if interval < self._keepalive_interval:
            interval = self._keepalive_interval
        return interval

    def _maybe_request_window(self, now):
        """Request a larger window if conditions allow."""
        if self._window_final:
            return
        interval = self._window_growth_interval()
        # Retry initial negotiation even without ACK progress.
        if not self._window_negotiated:
            if now - self._last_window_request_time >= interval:
                self.control.send_message(tun_window(self._proposed_window))
                self._last_window_request_time = now
                if self._logger.isEnabledFor(logging.INFO):
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
        if now - self._last_window_request_time < interval:
            return
        if self.negotiated_window >= self._proposed_window:
            return

        current = self.negotiated_window
        requested = current + self._window_growth_step

        requested = min(requested, self._proposed_window, self.MAX_WINDOW)
        if requested <= current:
            return

        self.control.send_message(tun_window(requested))
        self._last_window_request_time = now
        self._ack_progressed = False
        if self._logger.isEnabledFor(logging.INFO):
            log_event(
                self._logger,
                logging.INFO,
                'tunnel.window_propose',
                'Window growth request',
                lambda: {
                    'size': requested,
                    'current': current,
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
                if self._bg_stop:
                    return
                if self._logger.isEnabledFor(logging.WARNING):
                    log_event(
                        self._logger,
                        logging.WARNING,
                        'tunnel.tick_error',
                        'Tick error',
                        lambda: {'error': str(e), 'side': 'alice'},
                        exc_info=True,
                    )
                try:
                    e._bg_logged = True
                except Exception:
                    pass
                raise
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
