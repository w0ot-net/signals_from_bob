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
from . import codec
from .dns_utils import load_system_resolvers
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
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
        self._payload_cap = None

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

        # Calculate MTUs
        self._send_packet_mtu = codec.calc_query_mtu(
            self._base_domain, self._label_max_len
        )
        self._recv_packet_mtu = codec.calc_response_mtu(
            self._rtype,
            config.dns_edns_size,
            self._cname_suffix,
            self._label_max_len,
        )
        if self._rtype == codec.QTYPE_CNAME and self._edns_size <= 512:
            self._payload_cap = codec.calc_cname_payload_cap(
                self._base_domain,
                self._cname_suffix,
                self._label_max_len,
                self._edns_size,
            )
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.payload_cap',
                'DNS payload cap',
                lambda: {'payload_cap': self._payload_cap},
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
    def payload_cap(self):
        return self._payload_cap

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
        return self._reserve_permit(now=now, pending_before=pending_before)

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
        if qname is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.error_response',
                'DNS error response',
                lambda: {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'expected_qname': pending.qname,
                    'actual_qname': qname,
                    'rcode': rcode,
                    'reason': reason,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            # Clean up tracking to avoid pending exhaustion
            self._pending.pop(corr_id)
            del self._dns_to_corr[dns_id]
            return (None, None)
        if pending is not None and pending.qname != qname:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.mismatched_response',
                'DNS response qname mismatch',
                lambda: {
                    'dns_id': dns_id,
                    'corr_id': corr_id,
                    'expected': pending.qname,
                    'actual': qname,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)

        if payload is None:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.error_response',
                'DNS error response',
                lambda: {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'expected_qname': pending.qname,
                    'actual_qname': qname,
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

        log_event(
            _LOG,
            logging.DEBUG,
            'dns.recv',
            'DNS response received',
            lambda: {'corr_id': corr_id, 'dns_id': dns_id, 'bytes': len(payload)},
        )
        return (corr_id, payload)

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
