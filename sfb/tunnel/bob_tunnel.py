# -*- coding: ascii -*-
"""
Bob's tunnel implementation (server side).

Bob waits for Alice's requests and responds using a request/response
transport server.
"""

from __future__ import absolute_import

import logging

from .base_tunnel import BaseTunnel, TunnelState, TunnelError
from ..logging_util import log_event
from .. import time_provider
from ..protocol import (
    Packet,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
    FLAG_WANTS_POLL,
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

        send_payload, recv_payload = self._init_transport_limits(transport)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.init',
            'Tunnel init',
            lambda: {
                'transport_send_packet_mtu': transport.send_packet_mtu,
                'transport_recv_packet_mtu': transport.recv_packet_mtu,
                'send_payload': send_payload,
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
        self._serve_forever_active = False

    def serve_forever(self):
        """
        Serve requests until closed or idle timeout.
        """
        if self._bg_thread is not None and self._bg_thread.is_alive():
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.serve_conflict',
                'serve_forever called while background loop is running',
                lambda: {'side': 'bob'},
            )
        log_event(
            self._logger,
            logging.INFO,
            'tunnel.wait',
            'Waiting for connections',
            lambda: {'side': 'bob'},
        )

        self._serve_forever_active = True
        try:
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
        finally:
            self._serve_forever_active = False

    def start_background(self):
        if self._serve_forever_active:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.serve_conflict',
                'start_background called while serve_forever is running',
                lambda: {'side': 'bob'},
            )
        super(BobTunnel, self).start_background()

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
        now = time_provider.now()
        self._update_poll_ewma(now)

        # Decode incoming packet
        packet, packet_size = self._decode_packet(data, return_size=True)
        if packet is None:
            return

        self._bytes_received += len(data)

        # Handle based on state
        if self._state == TunnelState.DISCONNECTED:
            self._handle_handshake(packet, responder, now, packet_size=packet_size)
        elif self._state == TunnelState.CONNECTING:
            self._handle_handshake(packet, responder, now, packet_size=packet_size)
        elif self._state == TunnelState.CONNECTED:
            self._handle_data(packet, responder, now, packet_size=packet_size)
        else:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.request_state_unexpected',
                'Request in unexpected state',
                lambda: {'state': self._state, 'side': 'bob'},
            )

    def _handle_handshake(self, packet, responder, now, packet_size=None):
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
            if self._state != TunnelState.CONNECTING or self._local_isn is None:
                return
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
                self._process_incoming_packet(
                    packet, now=now, packet_size=packet_size
                )

                # Send response
                self._send_response(responder, now)

        elif self._state == TunnelState.CONNECTING:
            log_event(
                self._logger,
                logging.WARNING,
                'tunnel.handshake_invalid',
                'Non-handshake packet while connecting',
                lambda: {'side': 'bob', 'flags': packet.flags},
            )

    def _handle_data(self, packet, responder, now, packet_size=None):
        """Handle data packets."""
        # Process incoming
        self._process_incoming_packet(packet, now=now, packet_size=packet_size)

        # Send response
        self._send_response(responder, now)

    def _send_retransmit_response(self, responder, response_payload_cap, now,
                                  seq, segments, flags, encrypted_body,
                                  context='retransmit', reason=None):
        packet = self._rebuild_packet(seq, segments, flags=flags)
        encrypted_body, response_data = self._encode_packet_for_send(
            packet,
            encrypted_body=encrypted_body,
        )
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
        self._send_window.mark_retransmit(seq, now=now)
        def build_fields():
            fields = {
                'seq': seq,
                'ack': packet.ack,
                'sack': packet.sack,
                'flags': packet.flags,
                'seg_count': len(segments),
                'bytes': len(response_data),
                'side': 'bob',
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
        self._log_response_cap(responder, response_data)

        self._packets_sent += 1
        self._bytes_sent += len(response_data)
        self._respond(responder, response_data, context, packet)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            lambda: self._packet_send_fields(
                packet,
                len(response_data),
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

    def _send_poll_hint_response(self, responder, now, context):
        packet, _ = self._build_packet(
            flags=FLAG_WANTS_POLL,
            segments=[],
        )
        encrypted_body, response_data = self._encode_packet_for_send(packet)
        self._send_window.send(
            [],
            flags=packet.flags,
            encrypted_body=encrypted_body,
            now=now,
        )
        self._packets_sent += 1
        self._bytes_sent += len(response_data)
        self._respond(responder, response_data, context, packet)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            lambda: self._packet_send_fields(
                packet,
                len(response_data),
                context,
            ),
        )

    def _send_keepalive_response(self, responder, now):
        packet, _ = self._build_packet(
            flags=FLAG_KEEPALIVE,
            segments=[],
        )
        encrypted_body, response_data = self._encode_packet_for_send(packet)
        self._send_window.send(
            [],
            flags=packet.flags,
            encrypted_body=encrypted_body,
            now=now,
        )
        self._packets_sent += 1
        self._bytes_sent += len(response_data)
        self._log_response_cap(responder, response_data)
        self._respond(responder, response_data, 'keepalive', packet)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            lambda: self._packet_send_fields(
                packet,
                len(response_data),
                'keepalive',
            ),
        )

    def _send_segments_response(self, responder, now, segments):
        packet, _ = self._build_packet(
            flags=FLAG_HAS_SEGMENTS,
            segments=segments,
        )
        encrypted_body, response_data = self._encode_packet_for_send(packet)
        self._send_window.send(
            segments,
            flags=packet.flags,
            encrypted_body=encrypted_body,
            now=now,
        )
        self._packets_sent += 1
        self._bytes_sent += len(response_data)
        self._log_response_cap(responder, response_data)
        self._respond(responder, response_data, 'segments', packet)
        log_event(
            self._logger,
            logging.DEBUG,
            'tunnel.packet_send',
            'Packet sent',
            lambda: self._packet_send_fields(
                packet,
                len(response_data),
                'segments',
            ),
        )

    def _select_response_action(self, now, response_payload_cap):
        decision = {'action': None}
        oldest_info = self._send_window.get_oldest_unacked_info()
        if oldest_info is not None:
            (seq, segments, flags, encrypted_body,
             send_time, retransmit_count) = oldest_info
            cooldown = self._retransmit_cooldown()
            age = None
            if send_time is not None:
                age = now - send_time
            since_cum_ack = self._send_window.ack_silence(now=now)
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
                        'last_cum_ack': self._send_window.last_cum_ack,
                        'retransmit_count': retransmit_count,
                        'poll_ewma': self._poll_interval_ewma,
                        'unacked': self._send_window.unacked_count,
                        'max_in_flight': self._send_window._max_in_flight,
                        'side': 'bob',
                    },
                )
            else:
                decision.update({
                    'action': 'retransmit',
                    'context': 'retransmit',
                    'seq': seq,
                    'segments': segments,
                    'flags': flags,
                    'encrypted_body': encrypted_body,
                })
                return decision

        if not self._send_window.can_send:
            decision.update({
                'action': 'window_blocked',
                'context': 'window_full',
                'reason': 'window_full',
                'oldest_info': oldest_info,
                'unacked': self._send_window.unacked_count,
                'max_in_flight': self._send_window._max_in_flight,
            })
            return decision

        exceeded, distance_info = self._send_window.distance_exceeded(
            max_window=self.MAX_WINDOW
        )
        if exceeded:
            decision.update({
                'action': 'distance_blocked',
                'context': 'window_distance',
                'reason': 'window_distance',
                'oldest_info': oldest_info,
                'distance_info': distance_info,
            })
            return decision

        max_payload = self._payload_mtu_from_packet(self._send_packet_mtu)
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
                decision.update({
                    'action': 'poll_hint',
                    'context': 'poll_hint',
                    'reason': 'pending_data',
                })
                return decision
            decision.update({
                'action': 'keepalive',
                'context': 'keepalive',
            })
            return decision

        decision.update({
            'action': 'segments',
            'context': 'segments',
            'segments': segments,
        })
        return decision

    def _log_send_blocked(self, decision, now):
        action = decision.get('action')
        if action == 'window_blocked':
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
                        'max_in_flight': max_in_flight,
                        'side': 'bob',
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
                    'side': 'bob',
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
                },
            )
            return

        if action == 'distance_blocked':
            distance_info = decision.get('distance_info')
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
                    'side': 'bob',
                }
                fields.update(self._send_window.distance_details(now=now))
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
                lambda: {
                    'distance': distance,
                    'distance_limit': distance_limit,
                    'buffered': buffered,
                    'unacked': unacked,
                    'max_in_flight': max_in_flight,
                    'effective_cap': effective_cap,
                    'last_cum_ack': last_cum_ack,
                    'next_seq': next_seq,
                    'side': 'bob',
                    'reason': 'window_distance',
                },
            )
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.reliability_state',
                'Reliability state after send blocked',
                now=now,
                extra_fields={
                    'context': 'send_blocked',
                    'reason': 'window_distance',
                    'distance': distance,
                    'distance_limit': distance_limit,
                },
            )
            return

    def _send_response(self, responder, now):
        """Build and send response packet."""
        response_payload_cap = None
        if hasattr(responder, 'payload_cap'):
            response_payload_cap = responder.payload_cap

        decision = self._select_response_action(now, response_payload_cap)
        action = decision.get('action')
        if action == 'retransmit':
            self._send_retransmit_response(
                responder,
                response_payload_cap,
                now,
                decision['seq'],
                decision['segments'],
                decision['flags'],
                decision['encrypted_body'],
                context=decision.get('context', 'retransmit'),
            )
            return
        if action in ('window_blocked', 'distance_blocked'):
            self._log_send_blocked(decision, now)
            oldest_info = decision.get('oldest_info')
            if oldest_info is not None:
                self._send_retransmit_response(
                    responder,
                    response_payload_cap,
                    now,
                    oldest_info[0],
                    oldest_info[1],
                    oldest_info[2],
                    oldest_info[3],
                    context=decision.get('context'),
                    reason=decision.get('reason'),
                )
            return
        if action == 'poll_hint':
            self._log_reliability_state(
                logging.DEBUG,
                'tunnel.keepalive_suppressed',
                'Keepalive suppressed by pending data',
                now=now,
                extra_fields={
                    'reason': decision.get('reason'),
                },
            )
            self._send_poll_hint_response(
                responder,
                now,
                decision.get('context', 'poll_hint'),
            )
            return
        if action == 'keepalive':
            self._send_keepalive_response(responder, now)
            return
        if action == 'segments':
            self._send_segments_response(
                responder,
                now,
                decision.get('segments'),
            )
            return

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

        elapsed = time_provider.now() - self._last_request_time
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
        poll_ewma = self._poll_interval_ewma
        if poll_ewma is not None and poll_ewma > 0:
            if factor > 0:
                cooldown = max(cooldown, poll_ewma * factor)
            window = getattr(self._send_window, '_max_in_flight', None)
            if window is not None and window > 0:
                cooldown = max(cooldown, poll_ewma * window)
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
