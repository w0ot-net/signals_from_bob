# -*- coding: ascii -*-
"""
DNS TXT client transport for Alice.

Encodes tunnel packets into DNS TXT queries and decodes TXT responses.
Supports pipelining with multiple in-flight queries.
"""

from __future__ import absolute_import

from collections import namedtuple
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
from . import dns_txt_codec as codec
from .dns_utils import load_system_resolvers
from ...compat import require_bytes_like
from ...config import Config
from ...logging_util import get_logger, log_event
from ...utils import parse_host_port
from ... import time_provider

_LOG = get_logger(__name__)

_PendingQuery = namedtuple('PendingQuery', ('dns_id', 'qname'))


class DnsTxtClient(Transport):
    """
    DNS TXT client transport for Alice.

    Sends tunnel packets as DNS TXT queries and receives responses.
    Supports pipelining - multiple queries in flight simultaneously.
    Responses are matched via correlation IDs mapped to DNS query IDs.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        super(DnsTxtClient, self).__init__()

        self._config = config
        self._base_domain = config.dns_base_domain.lower().rstrip('.')
        self._edns_size = config.dns_edns_size
        self._max_in_flight = config.max_in_flight
        self._pending_timeout = config.dns_pending_timeout
        self._label_max_len = config.dns_label_max_len
        self._nonce = random.randint(0, 0xFFFF)
        self._query_id = random.randint(0, 0xFFFF)

        resolver = config.dns_resolver
        if resolver:
            host, port = parse_host_port(resolver, default_port=53)
            self._resolver = (host, port)
        else:
            resolvers = load_system_resolvers()
            if not resolvers:
                raise TransportError('No system resolvers found')
            self._resolver = resolvers[0]

        if _LOG.isEnabledFor(logging.INFO):
            log_event(
                _LOG,
                logging.INFO,
                'dns_txt.client_config',
                'DNS TXT client config',
                lambda: {
                    'base_domain': self._base_domain,
                    'resolver': '%s:%d' % (self._resolver[0], self._resolver[1]),
                    'edns_size': self._edns_size,
                    'max_in_flight': self._max_in_flight,
                    'pending_timeout': self._pending_timeout,
                    'label_max_len': self._label_max_len,
                },
            )

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        if self._edns_size > 512:
            self._opt_record = codec.build_opt_record(self._edns_size)
            self._opt_arcount = 1
        else:
            self._opt_record = b''
            self._opt_arcount = 0

        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'dns_txt', config, role='client'
        )
        self._send_packet_mtu = send_mtu
        self._recv_packet_mtu = recv_mtu
        mtu_details = {
            'transport': 'dns_txt',
            'role': 'client',
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'min_packet_mtu': min_packet_mtu,
        }
        mtu_details.update(mtu_constraints)
        if _LOG.isEnabledFor(logging.INFO):
            log_event(
                _LOG,
                logging.INFO,
                'transport.mtu_limits',
                'Transport MTU limits',
                lambda: mtu_details,
            )
        self._recv_bufsize = max(self._edns_size, config.dns_recv_bufsize_min)

        self._next_corr_id = 0
        self._pending = PendingTracker(self._pending_timeout)
        self._dns_to_corr = [None] * 0x10000

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def max_in_flight(self):
        return self._max_in_flight

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
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.send_blocked',
                    'DNS TXT send blocked',
                    lambda: {
                        'pending': pending_before,
                        'reserved': reserved,
                        'pending_total': pending_total,
                        'max_in_flight': self._max_in_flight,
                    },
                )
            return None
        permit = self._reserve_permit(now=now, pending_before=pending_before)
        return permit

    def _send_impl(self, data, permit):
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

        corr_id = self._next_corr_id
        self._next_corr_id += 1
        dns_id = self._next_query_id()

        query_name = self._encode_query(data)
        query_pkt = self._build_query(dns_id, query_name)

        try:
            self._sock.sendto(query_pkt, self._resolver)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        pending = _PendingQuery(dns_id, query_name.lower())
        self._pending.add(corr_id, pending, now=permit.now)
        self._dns_to_corr[dns_id] = corr_id

        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns_txt.send',
                'DNS TXT query sent',
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
        prune_and_count(
            self._pending, self._prune_stale, on_prune=self._on_prune
        )
        if timeout == 0:
            try:
                ready, _, _ = select.select([self._sock], [], [], 0)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if ready:
                return self._try_recv()
            return (None, None)

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
        try:
            resp_data, addr = self._sock.recvfrom(self._recv_bufsize)
        except socket.error as e:
            raise TransportError('Receive failed: %s' % e)

        result = self._parse_response(resp_data)
        if result is None:
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.malformed_response',
                    'DNS TXT response malformed',
                    lambda: {
                        'bytes': len(resp_data),
                        'addr': '%s:%d' % (addr[0], addr[1]),
                    },
                )
            return (None, None)

        dns_id = result.dns_id
        qname = result.qname
        payload = result.payload
        rcode = result.rcode
        reason = result.reason

        corr_id = self._dns_to_corr[dns_id]
        if corr_id is None:
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.stale_response',
                    'DNS TXT response stale',
                    lambda: {
                        'dns_id': dns_id,
                        'pending': len(self._pending),
                        'addr': '%s:%d' % (addr[0], addr[1]),
                    },
                )
            return (None, None)

        pending = self._pending.get(corr_id)
        if pending is None:
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.missing_pending',
                    'DNS TXT response missing pending entry',
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
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.mismatched_response',
                    'DNS TXT response qname mismatch',
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
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.error_response',
                    'DNS TXT error response',
                    lambda: {
                        'corr_id': corr_id,
                        'dns_id': dns_id,
                        'rcode': rcode,
                        'reason': reason,
                        'addr': '%s:%d' % (addr[0], addr[1]),
                    },
                )
            self._pending.pop(corr_id)
            self._dns_to_corr[dns_id] = None
            return (None, None)

        self._pending.pop(corr_id)
        self._dns_to_corr[dns_id] = None
        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns_txt.recv',
                'DNS TXT response received',
                lambda: {
                    'corr_id': corr_id,
                    'dns_id': dns_id,
                    'bytes': len(payload),
                },
            )
        return (corr_id, payload)

    def _on_prune(self, stale):
        for _, pending in stale:
            self._dns_to_corr[pending.dns_id] = None

    def _prune_stale(self, now=None):
        if now is None:
            now = time_provider.now()
        stale = self._pending.prune(now=now)
        if stale:
            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.prune_stale',
                    'Pruned stale DNS TXT queries',
                    lambda: {'count': len(stale)},
                )
        return stale

    def _encode_query(self, data):
        nonce = self._nonce
        self._nonce = (self._nonce + 1) & 0xFFFF
        return codec.encode_query_name(
            data,
            self._base_domain,
            nonce,
            self._label_max_len,
        )

    def _next_query_id(self):
        qid = self._query_id
        self._query_id = (self._query_id + 1) & 0xFFFF
        return qid

    def _build_query(self, query_id, name):
        header = struct.pack('>HHHHHH',
            query_id,
            codec.FLAG_RD,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            self._opt_arcount
        )
        qname = codec.encode_name(name)
        question = qname + struct.pack('>HH', codec.QTYPE_TXT, codec.QCLASS_IN)
        return header + question + self._opt_record

    def _parse_response(self, data):
        return codec.parse_txt_response(data)

    def close(self):
        self._pending.clear()
        self._dns_to_corr = [None] * 0x10000
        if self._sock:
            self._sock.close()
            self._sock = None
