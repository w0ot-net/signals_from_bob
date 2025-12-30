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
import time

from ..transport_base import Transport, TransportError, PendingTracker, TokenBucket
from . import codec
from .dns_utils import load_system_resolvers
from ...compat import require_bytes
from ...config import Config
from ...logging_util import get_logger, log_event

_LOG = get_logger(__name__)


class _PendingQuery(object):
    """Tracks an in-flight DNS query."""

    __slots__ = ('dns_id', 'query_pkt', 'qname')

    def __init__(self, dns_id, query_pkt, qname):
        self.dns_id = dns_id
        self.query_pkt = query_pkt
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

        self._config = config
        self._base_domain = config.dns_base_domain.lower().rstrip('.')
        self._qtype = codec.RECORD_TYPES[config.dns_query_type]
        self._rtype = codec.RECORD_TYPES[config.dns_response_type]
        self._edns_size = config.dns_edns_size
        self._max_pending = config.dns_max_pending
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
            if ':' in resolver:
                host, port = resolver.rsplit(':', 1)
                self._resolver = (host, int(port))
            else:
                self._resolver = (resolver, 53)
        else:
            resolvers = load_system_resolvers()
            if not resolvers:
                raise TransportError('No system resolvers found')
            self._resolver = resolvers[0]

        # Create non-blocking UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        # Calculate MTUs
        self._send_mtu = codec.calc_query_mtu(self._base_domain,
                                              self._label_max_len)
        self._recv_mtu = codec.calc_response_mtu(self._rtype,
                                                 config.dns_edns_size,
                                                 self._cname_suffix,
                                                 self._label_max_len)
        if self._rtype == codec.QTYPE_CNAME and self._edns_size <= 512:
            self._payload_cap = codec.calc_cname_payload_cap(
                self._base_domain,
                self._cname_suffix,
                self._label_max_len,
                self._edns_size,
            )
            _LOG.debug('dns payload cap=%d', self._payload_cap)
        self._recv_bufsize = max(self._edns_size, config.dns_recv_bufsize_min)

        # Pending query tracking
        self._next_corr_id = 0
        self._pending = PendingTracker(self._pending_timeout)
        self._dns_to_corr = {}  # dns_id -> corr_id

        # Rate limiting (token bucket)
        self._qps_limit = config.dns_queries_per_second
        if self._qps_limit > 0:
            self._rate_limiter = TokenBucket(self._qps_limit)
        else:
            self._rate_limiter = None

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    @property
    def payload_cap(self):
        return self._payload_cap

    @property
    def max_pending(self):
        return self._max_pending

    def pending_count(self):
        """Return number of queries awaiting response."""
        self._prune_stale()
        return len(self._pending)

    def can_send(self):
        """
        Check if rate limit allows sending.

        Returns:
            bool: True if a query can be sent without exceeding rate limit
        """
        if self.pending_count() >= self._max_pending:
            return False
        if self._rate_limiter is None:
            return True  # Unlimited
        return self._rate_limiter.can_take(1.0, now=time.time())

    def send(self, data):
        """
        Send data as DNS query.

        Args:
            data: bytes to send

        Returns:
            int: Correlation ID for matching response

        Raises:
            TransportError: on I/O failure or MTU exceeded
        """
        if not self.can_send():
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.send_blocked',
                'DNS send blocked',
                {
                    'pending': self.pending_count(),
                    'max_pending': self._max_pending,
                },
            )
            raise TransportError('Rate limit exceeded or too many pending queries')
        data = require_bytes(data)
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )

        # Prune stale pending queries before sending
        self._prune_stale()

        # Generate IDs
        corr_id = self._next_corr_id
        self._next_corr_id += 1
        dns_id = self._next_query_id()

        # Build query
        query_name = self._encode_query(data)
        query_pkt = self._build_query(dns_id, query_name)

        # Send query
        try:
            _LOG.debug('dns send corr=%d dns_id=%d resolver=%s',
                       corr_id, dns_id, self._resolver)
            self._sock.sendto(query_pkt, self._resolver)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        # Track pending
        pending = _PendingQuery(dns_id, query_pkt, query_name.lower())
        self._pending.add(corr_id, pending)
        self._dns_to_corr[dns_id] = corr_id

        # Consume a token for rate limiting
        if self._rate_limiter is not None:
            self._rate_limiter.take(1.0, now=time.time())

        log_event(
            _LOG,
            logging.DEBUG,
            'dns.send',
            'DNS query sent',
            {
                'corr_id': corr_id,
                'dns_id': dns_id,
                'resolver': '%s:%d' % (self._resolver[0], self._resolver[1]),
                'bytes': len(query_pkt),
                'payload_bytes': len(data),
                'pending': self.pending_count(),
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
        self._prune_stale()
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
            deadline = time.time() + timeout
        while True:
            if timeout is None:
                wait = None
            else:
                remaining = deadline - time.time()
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
                {
                    'bytes': len(resp_data),
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)  # Malformed packet

        dns_id, qname, payload, rcode, reason = result

        if dns_id not in self._dns_to_corr:
            _LOG.debug('dns stale response dns_id=%d', dns_id)
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.stale_response',
                'DNS response stale',
                {
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
                {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)
        if pending is not None and pending.qname != qname:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.mismatched_response',
                'DNS response qname mismatch',
                {
                    'dns_id': dns_id,
                    'corr_id': corr_id,
                    'expected': pending.qname,
                    'actual': qname,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                },
            )
            return (None, None)

        if payload is None:
            _LOG.debug('dns error response corr=%d dns_id=%d', corr_id, dns_id)
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.error_response',
                'DNS error response',
                {
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

        _LOG.debug('dns recv corr=%d dns_id=%d len=%d', corr_id, dns_id, len(payload))
        log_event(
            _LOG,
            logging.DEBUG,
            'dns.recv',
            'DNS response received',
            {'corr_id': corr_id, 'dns_id': dns_id, 'bytes': len(payload)},
        )
        return (corr_id, payload)

    def _prune_stale(self, now=None):
        """Remove stale pending queries to free capacity."""
        if now is None:
            now = time.time()
        stale = self._pending.prune(now=now)
        if stale:
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.prune_stale',
                'Pruned stale DNS queries',
                {'count': len(stale)},
            )
        for cid, pending in stale:
            dns_id = pending.dns_id
            self._dns_to_corr.pop(dns_id, None)

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
        # Include OPT record for EDNS0 if enabled
        if self._edns_size > 512:
            arcount = 1
            additional = codec.build_opt_record(self._edns_size)
        else:
            arcount = 0
            additional = b''

        header = struct.pack('>HHHHHH',
            query_id,
            codec.FLAG_RD,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            arcount
        )
        qname = codec.encode_name(name)
        question = qname + struct.pack('>HH', self._qtype, codec.QCLASS_IN)
        return header + question + additional

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
            _LOG.debug('dns rcode=%d id=%d', rcode, query_id)
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
                offset = codec.skip_name(data, offset)  # NAME
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

            try:
                cname, end_offset = codec.decode_name(
                    data, offset, allow_compression=True
                )
            except ValueError:
                _LOG.debug('dns invalid cname id=%d', query_id)
                return query_id, qname, None, rcode, 'cname_decode'

            if end_offset > offset + rdlength:
                _LOG.debug('dns cname exceeds rdlength id=%d', query_id)
                return query_id, qname, None, rcode, 'cname_rdlength'

            try:
                payload = codec.decode_cname_target(
                    cname, self._cname_suffix, self._label_max_len
                )
            except ValueError:
                _LOG.debug('dns invalid cname payload id=%d', query_id)
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
