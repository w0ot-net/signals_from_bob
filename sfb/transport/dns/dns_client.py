# -*- coding: ascii -*-
"""
DNS client transport for Alice.

Encodes tunnel packets into DNS A queries and decodes CNAME responses.
Supports pipelining with multiple in-flight queries.
"""

from __future__ import absolute_import

import logging
import random
import select
import socket
import struct

from ..transport_base import (
    Transport,
    TransportError,
    PendingTracker,
    prune_and_count,
)
from ..mtu_limits import resolve_mtu_limits
from . import dns_codec as codec
from .dns_utils import load_system_resolvers
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ...protocol import (
    PacketHeader,
    FLAG_SYN,
    FLAG_ACK,
    FLAG_KEEPALIVE,
    FLAG_HAS_SEGMENTS,
    FLAG_POLL_HINT,
)
from ...protocol.constants import MIN_PACKET_MTU, PACKET_HEADER_SIZE
from ...utils import parse_host_port
from ... import time_provider

_LOG = get_logger(__name__)


class _PendingQuery(object):
    """Tracks an in-flight DNS query."""

    __slots__ = ('dns_id', 'qname')

    def __init__(self, dns_id, qname):
        self.dns_id = dns_id
        self.qname = qname


class DnsClient(Transport):
    """
    DNS client transport for Alice.

    Sends tunnel packets as DNS A queries and receives responses.
    Supports pipelining - multiple queries in flight simultaneously.
    Responses are matched via correlation IDs mapped to DNS query IDs.
    """

    def __init__(self, config):
        """
        Initialize DNS client transport.

        Args:
            config: Config instance with dns_* settings
        """
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(DnsClient, self).__init__()

        self._config = config
        self._base_domain = config.dns_base_domain.lower().rstrip('.')
        self._qtype = codec.RECORD_TYPES[config.dns_query_type]
        self._rtype = codec.RECORD_TYPES[config.dns_response_type]
        self._edns_size = config.dns_edns_size
        self._max_in_flight = config.max_in_flight
        self._pending_timeout = config.dns_pending_timeout
        self._label_max_len = config.dns_label_max_len
        self._cname_suffix = '%s.%s' % (
            config.dns_cname_label.strip('.'),
            self._base_domain
        )
        self._nonce = random.randint(0, 0xFFFF)
        self._query_id = random.randint(0, 0xFFFF)
        self._alice_has_data_pending = False
        self._bob_has_data_polls = 2
        self._bob_has_data_remaining = 0
        self._poll_hint_budget = 0
        self._poll_hint_mode = None
        self._retransmit_guard = False
        self._recv_window_sack = 0

        # Parse resolver address or use system resolver
        resolver = config.dns_resolver
        if resolver:
            host, port = parse_host_port(resolver, default_port=53)
            self._resolver = (host, port)
        else:
            resolvers = load_system_resolvers()
            if not resolvers:
                raise TransportError('No system resolvers found')
            self._resolver = resolvers[0]

        log_event(
            _LOG,
            logging.INFO,
            'dns.client_config',
            'DNS client config',
            lambda: {
                'base_domain': self._base_domain,
                'resolver': '%s:%d' % (self._resolver[0], self._resolver[1]),
                'qtype': config.dns_query_type,
                'rtype': config.dns_response_type,
                'edns_size': self._edns_size,
                'max_in_flight': self._max_in_flight,
                'pending_timeout': self._pending_timeout,
                'label_max_len': self._label_max_len,
                'cname_suffix': self._cname_suffix,
            },
        )

        # Create non-blocking UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        # Cache EDNS0 OPT record for queries.
        if self._edns_size > 512:
            self._opt_record = codec.build_opt_record(self._edns_size)
            self._opt_arcount = 1
        else:
            self._opt_record = b''
            self._opt_arcount = 0
        self._opt_record_len = len(self._opt_record)
        # Calculate MTUs
        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'dns', config, role='client'
        )
        self._raw_query_packet_mtu = send_mtu
        self._send_packet_mtu = send_mtu
        calculated_recv_mtu = recv_mtu
        self._min_query_packet_mtu = min_packet_mtu
        self._min_response_packet_mtu = min_packet_mtu
        self._response_cap_lookup = None
        self._max_response_payload_cap = None
        self._max_response_packet_mtu = None
        self._safe_query_payload = None
        self._unsafe_query_payload = None
        self._balanced_query_payload = None
        self._last_unsafe_fallback_log = None
        if self._rtype == codec.QTYPE_CNAME:
            self._init_response_caps()
            effective_recv_mtu = min(
                calculated_recv_mtu,
                self._max_response_packet_mtu,
            )
            if effective_recv_mtu < calculated_recv_mtu:
                log_event(
                    _LOG,
                    logging.INFO,
                    'dns.mtu_clamp',
                    'DNS response MTU clamped',
                    lambda: {
                        'calculated_mtu': calculated_recv_mtu,
                        'max_response_packet_mtu': self._max_response_packet_mtu,
                        'effective_mtu': effective_recv_mtu,
                    },
                )
            self._recv_packet_mtu = effective_recv_mtu
        else:
            self._recv_packet_mtu = calculated_recv_mtu
        mtu_details = {
            'transport': 'dns',
            'role': 'client',
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'min_packet_mtu': min_packet_mtu,
            'raw_query_packet_mtu': self._raw_query_packet_mtu,
        }
        mtu_details.update(mtu_constraints)
        log_event(
            _LOG,
            logging.INFO,
            'transport.mtu_limits',
            'Transport MTU limits',
            lambda: mtu_details,
        )
        self._recv_bufsize = max(self._edns_size, config.dns_recv_bufsize_min)

        # Pending query tracking
        self._next_corr_id = 0
        self._pending = PendingTracker(self._pending_timeout)
        self._dns_to_corr = {}  # dns_id -> corr_id

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def max_in_flight(self):
        return self._max_in_flight

    def pending_count(self):
        """Return number of queries awaiting response."""
        return len(self._pending)

    def reserve_send(self, now=None):
        if now is None:
            now = time_provider.now()
        pending_before = prune_and_count(
            self._pending, self._prune_stale, now=now, on_prune=self._on_prune
        )
        self._ensure_reserved()
        reserved = len(self._reserved)
        pending_total = pending_before + reserved
        if pending_total >= self._max_in_flight:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.send_blocked',
                'DNS send blocked',
                lambda: {
                    'pending': pending_before,
                    'reserved': reserved,
                    'pending_total': pending_total,
                    'max_in_flight': self._max_in_flight,
                },
            )
            return None
        permit = self._reserve_permit(now=now, pending_before=pending_before)
        payload_cap = self._select_payload_cap()
        self._attach_payload_cap(permit, payload_cap)
        self._consume_poll_hint_budget()
        return permit

    def payload_cap_for_send(self, permit):
        if permit is None:
            return None
        data = permit.data
        if isinstance(data, dict) and 'sfb_payload_cap' in data:
            return data.get('sfb_payload_cap')
        return None

    def notify_send_pending(self, has_data):
        self._alice_has_data_pending = bool(has_data)

    def notify_peer_data(self, has_data):
        if has_data:
            self._update_bob_data_state(True)

    def notify_recv_window_sack(self, sack):
        if sack:
            self._recv_window_sack = sack
        else:
            self._recv_window_sack = 0

    def _send_impl(self, data, permit):
        """
        Send data as DNS query.

        Args:
            data: bytes to send
            permit: SendPermit reserved by this transport

        Returns:
            int: Correlation ID for matching response

        Raises:
            TransportError: on I/O failure or MTU exceeded
        """
        pending_before = permit.pending_before
        if pending_before is None:
            pending_before = len(self._pending)
        data = require_bytes_like(data)
        if len(data) > self._send_packet_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (
                    len(data), self._send_packet_mtu
                )
            )

        # Generate IDs
        corr_id = self._next_corr_id
        self._next_corr_id += 1
        dns_id = self._next_query_id()

        # Build query
        query_name = self._encode_query(data)
        query_pkt = self._build_query(dns_id, query_name)

        # Send query
        try:
            self._sock.sendto(query_pkt, self._resolver)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        # Track pending
        pending = _PendingQuery(dns_id, query_name.lower())
        self._pending.add(corr_id, pending, now=permit.now)
        self._dns_to_corr[dns_id] = corr_id

        log_event(
            _LOG,
            logging.DEBUG,
            'dns.send',
            'DNS query sent',
            lambda: {
                'corr_id': corr_id,
                'dns_id': dns_id,
                'resolver': '%s:%d' % (self._resolver[0], self._resolver[1]),
                'bytes': len(query_pkt),
                'payload_bytes': len(data),
                'pending': pending_before + 1,
            },
        )
        return corr_id

    def recv(self, timeout=None):
        """
        Receive next available response.

        Args:
            timeout: Max seconds to wait
                     None = block until response
                     0 = non-blocking poll

        Returns:
            tuple: (correlation_id, data) on success
                   (None, None) on timeout

        Raises:
            TransportError: on I/O failure
        """
        prune_and_count(
            self._pending, self._prune_stale, on_prune=self._on_prune
        )
        if timeout == 0:
            # Non-blocking poll
            try:
                ready, _, _ = select.select([self._sock], [], [], 0)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if ready:
                return self._try_recv()
            return (None, None)
        # Wait up to timeout (or indefinitely if timeout is None)
        deadline = None
        if timeout is not None:
            deadline = time_provider.now() + timeout
        while True:
            if timeout is None:
                wait = None
            else:
                remaining = deadline - time_provider.now()
                if remaining <= 0:
                    return (None, None)
                wait = remaining
            try:
                ready, _, _ = select.select([self._sock], [], [], wait)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if not ready:
                if timeout is None:
                    continue
                return (None, None)
            result = self._try_recv()
            if result[0] is not None:
                return result

    def _try_recv(self):
        """
        Try to receive and parse one response.

        Returns:
            tuple: (correlation_id, data) on success
                   (None, None) if no valid response available
        """
        try:
            resp_data, addr = self._sock.recvfrom(self._recv_bufsize)
        except socket.error as e:
            raise TransportError('Receive failed: %s' % e)

        result = self._parse_response(resp_data)
        if result is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.malformed_response',
                'DNS response malformed',
                lambda: {
                    'bytes': len(resp_data),
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)  # Malformed packet

        dns_id, qname, payload, rcode, reason = result

        if dns_id not in self._dns_to_corr:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.stale_response',
                'DNS response stale',
                lambda: {
                    'dns_id': dns_id,
                    'pending': len(self._pending),
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)  # Stale or unknown query

        corr_id = self._dns_to_corr[dns_id]
        pending = self._pending.get(corr_id)
        if pending is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.missing_pending',
                'DNS response missing pending entry',
                lambda: {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)
        error_response = False
        if qname is None:
            error_response = True
        elif pending.qname != qname:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.mismatched_response',
                'DNS response qname mismatch',
                lambda: {
                    'dns_id': dns_id,
                    'corr_id': corr_id,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)

        if payload is None:
            error_response = True

        if error_response:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.error_response',
                'DNS error response',
                lambda: {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'rcode': rcode,
                    'reason': reason,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            # Clean up tracking to avoid pending exhaustion
            self._pending.pop(corr_id)
            del self._dns_to_corr[dns_id]
            return (None, None)  # RCODE error, drop

        # Clean up tracking
        self._pending.pop(corr_id)
        del self._dns_to_corr[dns_id]

        self._update_bob_data_from_payload(payload)
        log_event(
            _LOG,
            logging.DEBUG,
            'dns.recv',
            'DNS response received',
            lambda: {'corr_id': corr_id, 'dns_id': dns_id, 'bytes': len(payload)},
        )
        return (corr_id, payload)

    def _update_bob_data_from_payload(self, payload):
        if not payload:
            return
        try:
            header = PacketHeader.decode(payload)
        except ValueError as exc:
            self._log_clamp_header_skip('decode_error', payload, error=str(exc))
            return
        flags = header.flags
        if flags & (FLAG_SYN | FLAG_ACK):
            self._log_clamp_header_skip(
                'handshake_flags',
                payload,
                flags=flags,
            )
            return
        has_keepalive = bool(flags & FLAG_KEEPALIVE)
        has_segments = bool(flags & FLAG_HAS_SEGMENTS)
        if not has_keepalive and not has_segments:
            self._log_clamp_header_skip(
                'missing_content_flags',
                payload,
                flags=flags,
            )
            return
        if has_keepalive and has_segments:
            self._log_clamp_header_skip(
                'multiple_content_flags',
                payload,
                flags=flags,
            )
            return
        poll_hint = bool(flags & FLAG_POLL_HINT)
        if poll_hint:
            if has_segments:
                self._reset_poll_hint_budget('segments')
            else:
                self._reset_poll_hint_budget('keepalive')
        if has_segments:
            self._update_bob_data_state(True)
            return
        self._update_bob_data_state(False)

    def _log_clamp_header_skip(self, reason, payload, flags=None, error=None):
        log_event(
            _LOG,
            logging.DEBUG,
            'dns.clamp_header_skip',
            'DNS clamp header unusable',
            lambda: {
                'reason': reason,
                'flags': flags,
                'error': error,
                'payload_bytes': len(payload),
            },
        )

    def _on_prune(self, stale):
        for _, pending in stale:
            self._dns_to_corr.pop(pending.dns_id, None)

    def _prune_stale(self, now=None):
        """Remove stale pending queries to free capacity."""
        if now is None:
            now = time_provider.now()
        stale = self._pending.prune(now=now)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.prune_stale',
                'Pruned stale DNS queries',
                lambda: {'count': len(stale)},
            )
        return stale

    def _attach_payload_cap(self, permit, payload_cap):
        if payload_cap is None:
            return
        if permit.data is None:
            permit.data = {}
        if not isinstance(permit.data, dict):
            return
        permit.data['sfb_payload_cap'] = payload_cap

    def _select_payload_cap(self):
        if self._response_cap_lookup is None:
            return None
        safe_query_payload = self._safe_query_payload
        if safe_query_payload is None:
            safe_query_payload = self._send_packet_mtu
        unsafe_query_payload = self._unsafe_query_payload
        mode = 'clamp_safe_max_alice'
        target = None
        query_payload = safe_query_payload
        fallback = None
        if self._poll_hint_budget > 0:
            if self._poll_hint_mode == 'segments' and self._alice_has_data_pending:
                mode = 'clamp_balanced'
                query_payload = self._balanced_query_payload
                if query_payload is None:
                    mode = 'clamp_max_bob'
                    fallback = 'balanced_unavailable'
            else:
                mode = 'clamp_max_bob'
            if mode == 'clamp_max_bob':
                target = self._max_response_payload_cap
                query_payload = self._max_query_payload_for_response_cap(target)
                if query_payload is None:
                    query_payload = safe_query_payload
                    fallback = 'missing_response_cap'
        else:
            if (self._alice_has_data_pending and
                    self._bob_has_data_remaining <= 0):
                mode = 'clamp_unsafe_alice_max'
                target = PACKET_HEADER_SIZE
                query_payload = unsafe_query_payload
                if (query_payload is None or
                        query_payload < self._min_query_packet_mtu):
                    mode = 'clamp_safe_max_alice'
                    query_payload = safe_query_payload
                    fallback = 'unsafe_missing_header_cap'
                    self._log_unsafe_fallback(fallback)
        if query_payload is not None:
            if query_payload > self._raw_query_packet_mtu:
                query_payload = self._raw_query_packet_mtu
            if query_payload < self._min_query_packet_mtu:
                query_payload = self._min_query_packet_mtu
        log_event(
            _LOG,
            logging.DEBUG,
            'dns.clamp_select',
            'DNS clamp selected',
            lambda: {
                'mode': mode,
                'alice_has_data': self._alice_has_data_pending,
                'bob_has_data_remaining': self._bob_has_data_remaining,
                'retransmit_guard': self._retransmit_guard,
                'recv_window_sack': self._recv_window_sack,
                'raw_query_packet_mtu': self._raw_query_packet_mtu,
                'safe_query_payload': safe_query_payload,
                'unsafe_query_payload': unsafe_query_payload,
                'target_response_payload': target,
                'query_payload_cap': query_payload,
                'poll_hint_budget': self._poll_hint_budget,
                'poll_hint_mode': self._poll_hint_mode,
                'fallback': fallback,
            },
        )
        return query_payload

    def _log_unsafe_fallback(self, reason, now=None):
        log_now = now
        if log_now is None:
            log_now = time_provider.now()
        last_log = self._last_unsafe_fallback_log
        if last_log is None or (log_now - last_log) >= 2.0:
            self._last_unsafe_fallback_log = log_now
            log_event(
                _LOG,
                logging.INFO,
                'dns.clamp_unsafe_fallback',
                'DNS unsafe clamp unavailable; using safe max',
                lambda: {
                    'reason': reason,
                    'safe_query_payload': self._safe_query_payload,
                    'unsafe_query_payload': self._unsafe_query_payload,
                    'raw_query_mtu': self._raw_query_packet_mtu,
                    'min_query_mtu': self._min_query_packet_mtu,
                },
            )

    def _max_query_payload_for_response_cap(self, target_response_payload):
        """Return largest query payload that yields the target response cap."""
        if target_response_payload is None:
            return None
        lookup = self._response_cap_lookup
        if not lookup:
            return None
        if target_response_payload >= len(lookup):
            target_response_payload = len(lookup) - 1
        return lookup[target_response_payload]

    def _update_bob_data_state(self, has_data):
        if has_data:
            self._bob_has_data_remaining = self._bob_has_data_polls
            return
        if self._bob_has_data_remaining > 0:
            self._bob_has_data_remaining -= 1

    def _reset_poll_hint_budget(self, mode=None):
        target = 8
        if target < 1:
            target = 1
        self._poll_hint_budget = target
        self._poll_hint_mode = mode
        self._retransmit_guard = True

    def _consume_poll_hint_budget(self):
        if self._poll_hint_budget <= 0:
            return
        self._poll_hint_budget -= 1
        if self._poll_hint_budget <= 0:
            self._poll_hint_budget = 0
            self._retransmit_guard = False
            self._poll_hint_mode = None

    def _init_response_caps(self):
        raw_query_mtu = self._raw_query_packet_mtu
        if raw_query_mtu < self._min_query_packet_mtu:
            raise TransportError(
                'DNS query MTU %d below packet header size %d' % (
                    raw_query_mtu,
                    self._min_query_packet_mtu,
                )
            )
        max_query_payload = raw_query_mtu
        response_caps = [0] * (max_query_payload + 1)
        min_response_query_payload = None
        for payload_len in range(self._min_query_packet_mtu,
                                 max_query_payload + 1):
            try:
                qname_wire_len = codec.calc_qname_wire_len(
                    payload_len,
                    self._base_domain,
                    self._label_max_len,
                )
            except ValueError:
                continue
            response_cap, _ = codec.calc_cname_response_payload_cap(
                qname_wire_len,
                self._edns_size,
                self._cname_suffix,
                self._label_max_len,
                self._opt_record_len,
            )
            if response_cap is None:
                response_cap = 0
            response_caps[payload_len] = response_cap
            if response_cap >= MIN_PACKET_MTU:
                min_response_query_payload = payload_len
        if min_response_query_payload is None:
            raise TransportError(
                'DNS response payload cap below minimum (base_domain=%s, '
                'label_max_len=%d, edns_size=%d)' % (
                    self._base_domain,
                    self._label_max_len,
                    self._edns_size,
                )
            )
        self._safe_query_payload = min_response_query_payload
        if self._safe_query_payload < raw_query_mtu:
            log_event(
                _LOG,
                logging.INFO,
                'dns.query_safe_max',
                'DNS query safe max computed',
                lambda: {
                    'safe_query_payload': self._safe_query_payload,
                    'raw_query_mtu': raw_query_mtu,
                    'min_response_mtu': MIN_PACKET_MTU,
                    'base_domain': self._base_domain,
                    'label_max_len': self._label_max_len,
                    'edns_size': self._edns_size,
                },
            )
        max_response_payload = 0
        balanced_query_payload = None
        for payload_len in range(self._min_query_packet_mtu,
                                 max_query_payload + 1):
            response_cap = response_caps[payload_len]
            if response_cap > max_response_payload:
                max_response_payload = response_cap
            if response_cap >= payload_len:
                balanced_query_payload = payload_len
        self._max_response_payload_cap = max_response_payload
        self._max_response_packet_mtu = max_response_payload
        if self._max_response_packet_mtu < self._min_response_packet_mtu:
            raise TransportError(
                'DNS response MTU %d below minimum %d (base_domain=%s, '
                'label_max_len=%d, edns_size=%d)' % (
                    self._max_response_packet_mtu,
                    self._min_response_packet_mtu,
                    self._base_domain,
                    self._label_max_len,
                    self._edns_size,
                )
            )
        lookup = [0] * (self._max_response_packet_mtu + 1)
        for payload_len in range(self._min_query_packet_mtu,
                                 max_query_payload + 1):
            response_cap = response_caps[payload_len]
            if response_cap <= 0:
                continue
            if response_cap > self._max_response_packet_mtu:
                response_cap = self._max_response_packet_mtu
            for target in range(response_cap + 1):
                if payload_len > lookup[target]:
                    lookup[target] = payload_len
        if lookup[self._min_response_packet_mtu] < self._min_query_packet_mtu:
            raise TransportError(
                'DNS response MTU %d below minimum %d (base_domain=%s, '
                'label_max_len=%d, edns_size=%d)' % (
                    self._max_response_packet_mtu,
                    self._min_response_packet_mtu,
                    self._base_domain,
                    self._label_max_len,
                    self._edns_size,
                )
            )
        self._response_cap_lookup = lookup
        self._balanced_query_payload = balanced_query_payload
        unsafe_query_payload = self._max_query_payload_for_response_cap(
            PACKET_HEADER_SIZE
        )
        if (unsafe_query_payload is not None and
                unsafe_query_payload >= self._min_query_packet_mtu):
            self._unsafe_query_payload = unsafe_query_payload

    def _encode_query(self, data):
        """Encode data into DNS query name with nonce."""
        nonce = self._nonce
        self._nonce = (self._nonce + 1) & 0xFFFF
        return codec.encode_query_name(data, self._base_domain, nonce,
                                       self._label_max_len)

    def _next_query_id(self):
        """Generate next query ID."""
        qid = self._query_id
        self._query_id = (self._query_id + 1) & 0xFFFF
        return qid

    def _build_query(self, query_id, name):
        """Build DNS query packet."""
        header = struct.pack('>HHHHHH',
            query_id,
            codec.FLAG_RD,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            self._opt_arcount
        )
        qname = codec.encode_name(name)
        question = qname + struct.pack('>HH', self._qtype, codec.QCLASS_IN)
        return header + question + self._opt_record

    def _parse_response(self, data):
        """
        Parse DNS response packet.

        Returns:
            tuple: (query_id, qname, payload_bytes, rcode, reason) on success
            tuple: (query_id, qname, None, rcode, reason) if response has no payload
            tuple: (query_id, qname, None, None, reason) if packet is malformed
        """
        if len(data) < 12:
            return None

        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:12]
        )

        if not (flags & codec.FLAG_QR):
            return query_id, None, None, None, 'not_response'

        # Check RCODE
        rcode = flags & codec.RCODE_MASK
        if rcode != codec.RCODE_NOERROR:
            return query_id, None, None, rcode, 'rcode'

        # Skip questions
        offset = 12
        qname = None
        try:
            for _ in range(qdcount):
                if qname is None:
                    qname, offset = codec.decode_name(
                        data, offset, allow_compression=True
                    )
                    qname = qname.lower()
                else:
                    offset = codec.skip_name(data, offset)
                offset += 4  # QTYPE + QCLASS
        except ValueError:
            return query_id, None, None, None, 'question_parse'

        if qname is None:
            return query_id, None, None, None, 'question_parse'

        if ancount < 1:
            return query_id, qname, None, rcode, 'no_answer'

        for _ in range(ancount):
            try:
                answer_name, offset = codec.decode_name(
                    data, offset, allow_compression=True
                )
            except ValueError:
                return query_id, qname, None, rcode, 'answer_name'

            if offset + 10 > len(data):
                return query_id, qname, None, rcode, 'answer_header'

            rtype, rclass, ttl, rdlength = struct.unpack(
                '>HHIH', data[offset:offset + 10]
            )
            offset += 10

            if offset + rdlength > len(data):
                return query_id, qname, None, rcode, 'answer_rdlength'

            if rclass != codec.QCLASS_IN or rtype != self._rtype:
                offset += rdlength
                continue

            if answer_name.lower() != qname:
                offset += rdlength
                continue

            try:
                cname, end_offset = codec.decode_name(
                    data, offset, allow_compression=True
                )
            except ValueError:
                return query_id, qname, None, rcode, 'cname_decode'

            if end_offset > offset + rdlength:
                return query_id, qname, None, rcode, 'cname_rdlength'

            try:
                payload = codec.decode_cname_target(
                    cname, self._cname_suffix, self._label_max_len
                )
            except ValueError:
                return query_id, qname, None, rcode, 'payload_decode'

            return query_id, qname, payload, rcode, 'ok'

        return query_id, qname, None, rcode, 'no_matching_answer'

    def close(self):
        """Close the UDP socket and cancel all pending queries."""
        self._pending.clear()
        self._dns_to_corr.clear()
        if self._sock:
            self._sock.close()
            self._sock = None
