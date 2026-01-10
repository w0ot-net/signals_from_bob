# -*- coding: ascii -*-
"""
DNS server transport for Bob.

Receives tunnel packets from DNS A queries and sends CNAME responses.
"""

from __future__ import absolute_import

import logging
import select
import socket
import struct

from ..transport_base import Server, TransportError, raise_bind_error
from ..mtu_limits import resolve_mtu_limits
from . import dns_codec as codec
from .dns_flat_stager import DnsFlatStager
from ...config import Config
from ...logging_util import get_logger, log_event
from ...protocol.constants import MIN_PACKET_MTU
from ...utils import parse_host_port
from ... import time_provider


class _ResponseSender(object):
    def __init__(self, server, query_id, qname, qtype, addr,
                 response_payload_cap, qname_wire_len, max_packet_size):
        self._server = server
        self._query_id = query_id
        self._qname = qname
        self._qtype = qtype
        self._addr = addr
        self.response_payload_cap = response_payload_cap
        self.qname_wire_len = qname_wire_len
        self.max_packet_size = max_packet_size

    def __call__(self, data):
        self._server._send_response(
            self._query_id,
            self._qname,
            self._qtype,
            data,
            self._addr,
            response_payload_cap=self.response_payload_cap,
            qname_wire_len=self.qname_wire_len,
            max_packet_size=self.max_packet_size,
        )


class DnsServer(Server):
    """
    DNS server transport for Bob.

    Listens for DNS A queries and responds with CNAME records.
    """

    def __init__(self, config):
        """
        Initialize DNS server transport.

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
        self._label_max_len = config.dns_label_max_len
        self._cname_suffix = '%s.%s' % (
            config.dns_cname_label.strip('.'),
            self._base_domain
        )
        self._cname_suffix_lower = self._cname_suffix.lower()
        self._cname_a_addr = config.dns_cname_a_addr
        self._response_ttl = int(config.dns_response_ttl)
        if self._response_ttl < 0:
            raise ValueError('dns_response_ttl must be >= 0')
        # Parse listen address
        listen_addr = config.dns_listen_addr
        host, port = parse_host_port(listen_addr, default_port=53)
        self._listen_addr = (host, port)

        # Create and bind UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(self._listen_addr)
        except (socket.error, OSError) as exc:
            self._sock.close()
            self._sock = None
            raise_bind_error(exc, self._listen_addr, 'DNS')

        # Cache EDNS0 OPT record, recv buffer size, and SOA record.
        if self._edns_size > 512:
            self._opt_record = codec.build_opt_record(self._edns_size)
            self._opt_arcount = 1
        else:
            self._opt_record = b''
            self._opt_arcount = 0
        self._opt_record_len = len(self._opt_record)
        self._recv_bufsize = max(self._edns_size,
                                 self._config.dns_recv_bufsize_min)
        self._soa_record = self._build_soa_record()
        self._logger = get_logger(__name__)
        self._flat_stager = None
        if config.dns_flat_chunks:
            self._flat_stager = DnsFlatStager(
                base_domain=self._base_domain,
                stager_nonce=config.dns_stager_nonce,
                flat_chunks=config.dns_flat_chunks,
                flat_count=config.dns_flat_count,
                flat_meta=config.dns_flat_meta,
                flat_chunk_size=config.dns_flat_chunk_size,
                rtype=self._rtype,
                cname_suffix=self._cname_suffix,
                label_max_len=self._label_max_len,
                logger=self._logger,
                send_response=self._send_response,
                send_empty_response=self._send_empty_response,
            )
            if not self._flat_stager.enabled:
                self._flat_stager = None
        stager_enabled = bool(self._flat_stager)
        flat_chunks = config.dns_flat_chunks or []
        flat_count = config.dns_flat_count or 0
        stager_reason = None
        if not stager_enabled:
            if not flat_chunks:
                stager_reason = 'no_chunks'
            elif flat_count <= 0:
                stager_reason = 'count_zero'
            else:
                stager_reason = 'disabled'
        stager_fields = {
            'enabled': stager_enabled,
            'nonce': config.dns_stager_nonce,
            'flat_count': flat_count,
            'flat_chunk_size': config.dns_flat_chunk_size,
            'flat_meta_bytes': len(config.dns_flat_meta) if config.dns_flat_meta else 0,
            'chunks': len(flat_chunks),
        }
        if stager_reason:
            stager_fields['reason'] = stager_reason
        log_event(
            self._logger,
            logging.INFO,
            'dns.stager_config',
            'DNS stager config',
            lambda: stager_fields,
        )

        # Calculate MTUs
        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'dns', config, role='server'
        )
        self._recv_packet_mtu = recv_mtu
        calculated_send_mtu = send_mtu
        self._min_response_packet_mtu = min_packet_mtu
        self._max_response_packet_mtu = None
        if self._rtype == codec.QTYPE_CNAME:
            self._max_response_packet_mtu = (
                self._compute_max_response_packet_mtu()
            )
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
            effective_send_mtu = min(
                calculated_send_mtu,
                self._max_response_packet_mtu,
            )
            if effective_send_mtu < calculated_send_mtu:
                log_event(
                    self._logger,
                    logging.INFO,
                    'dns.mtu_clamp',
                    'DNS response MTU clamped',
                    lambda: {
                        'calculated_mtu': calculated_send_mtu,
                        'max_response_packet_mtu': self._max_response_packet_mtu,
                        'effective_mtu': effective_send_mtu,
                    },
                )
            self._send_packet_mtu = effective_send_mtu
        else:
            self._send_packet_mtu = calculated_send_mtu
        mtu_details = {
            'transport': 'dns',
            'role': 'server',
            'send_packet_mtu': self._send_packet_mtu,
            'recv_packet_mtu': self._recv_packet_mtu,
            'min_packet_mtu': min_packet_mtu,
        }
        mtu_details.update(mtu_constraints)
        log_event(
            self._logger,
            logging.INFO,
            'transport.mtu_limits',
            'Transport MTU limits',
            lambda: mtu_details,
        )
        log_event(
            self._logger,
            logging.INFO,
            'dns.server_config',
            'DNS server config',
            lambda: {
                'base_domain': self._base_domain,
                'listen_addr': '%s:%d' % (self._listen_addr[0], self._listen_addr[1]),
                'qtype': config.dns_query_type,
                'rtype': config.dns_response_type,
                'edns_size': self._edns_size,
                'label_max_len': self._label_max_len,
                'cname_suffix': self._cname_suffix,
                'response_ttl': self._response_ttl,
                'stager_nonce': config.dns_stager_nonce,
            },
        )
        try:
            self._cname_a_addr_bytes = socket.inet_aton(self._cname_a_addr)
        except (socket.error, OSError):
            log_event(
                self._logger,
                logging.WARNING,
                'dns.cname_invalid_addr',
                'Invalid cname_a_addr, using 0.0.0.0',
                lambda: {'addr': self._cname_a_addr},
            )
            self._cname_a_addr_bytes = b'\x00\x00\x00\x00'
        log_event(
            self._logger,
            logging.DEBUG,
            'dns.mtu_calc',
            'DNS MTU calc',
            lambda: {
                'base_domain': self._base_domain,
                'label_max': self._label_max_len,
                'recv_packet_mtu': self._recv_packet_mtu,
                'send_packet_mtu': self._send_packet_mtu,
            },
        )

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    def recv(self, timeout=None):
        """
        Wait for a DNS query from Alice.

        Args:
            timeout: max seconds to wait (None = block forever)

        Returns:
            tuple: (data, responder) where:
                - data: bytes received from Alice
                - responder: callable that takes bytes and sends response

            Returns (None, None) on timeout.

        Raises:
            TransportError: on I/O failure
        """
        deadline = None
        use_deadline = False
        if timeout is not None and timeout > 0:
            deadline = time_provider.now() + timeout
            use_deadline = True
        while True:
            try:
                if timeout is None:
                    wait = None
                elif timeout == 0:
                    wait = 0
                elif use_deadline:
                    remaining = deadline - time_provider.now()
                    if remaining <= 0:
                        return None, None
                    wait = remaining
                else:
                    wait = timeout
                ready, _, _ = select.select([self._sock], [], [], wait)
                if not ready:
                    return None, None
                pkt_data, client_addr = self._sock.recvfrom(self._recv_bufsize)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            except socket.error as e:
                raise TransportError('Receive failed: %s' % e)

            try:
                query_id, qname, qtype = self._parse_query(pkt_data)
            except (ValueError, TransportError) as e:
                # Malformed query, ignore
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'dns.invalid_query',
                    'DNS invalid query',
                    lambda: {'error': str(e)},
                )
                continue

            # Check if it's for our domain (subdomain or exact match)
            qname_lower = qname.lower()
            is_our_domain = (qname_lower == self._base_domain or
                             qname_lower.endswith('.' + self._base_domain))
            if not is_our_domain:
                # Not our query, ignore
                continue

            # Check query type
            if qtype != self._qtype:
                # Not a tunnel query, send empty response to avoid resolver timeouts
                self._send_empty_response(query_id, qname, qtype, client_addr,
                                          reason='qtype_mismatch')
                continue

            if self._flat_stager:
                if self._flat_stager.handle_query(
                        query_id, qname, qname_lower, qtype, client_addr):
                    continue

            if (qname_lower == self._cname_suffix_lower or
                    qname_lower.endswith('.' + self._cname_suffix_lower)):
                self._send_cname_followup(query_id, qname, qtype, client_addr)
                continue

            # Decode tunnel data
            try:
                data = codec.decode_query_name(qname, self._base_domain,
                                               self._label_max_len)
            except ValueError:
                # Decode failed, send empty response to avoid resolver timeouts
                self._send_empty_response(query_id, qname, qtype, client_addr,
                                          reason='decode_failed')
                continue

            response_payload_cap, qname_wire_len, max_packet_size = (
                self._response_payload_cap(qname)
            )
            responder = _ResponseSender(
                self, query_id, qname, qtype, client_addr, response_payload_cap,
                qname_wire_len, max_packet_size
            )

            log_event(
                self._logger,
                logging.DEBUG,
                'dns.recv',
                'DNS query received',
                lambda: {
                    'dns_id': query_id,
                    'qtype': qtype,
                    'addr': '%s:%d' % (client_addr[0], client_addr[1]),
                    'query_bytes': len(pkt_data),
                    'bytes': len(data),
                    'response_payload_cap': response_payload_cap,
                    'qname_wire_len': qname_wire_len,
                    'max_packet_size': max_packet_size,
                },
            )
            return data, responder

    def _parse_query(self, data):
        """
        Parse DNS query packet.

        Returns:
            tuple: (query_id, qname, qtype)

        Raises:
            ValueError: on parse error
        """
        if len(data) < 12:
            raise ValueError('Query too short')

        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:12]
        )

        # Should be a query (QR=0)
        if flags & codec.FLAG_QR:
            raise ValueError('Not a query')

        if qdcount < 1:
            raise ValueError('No question')

        # Parse question
        qname, offset = codec.decode_name(data, 12)

        if offset + 4 > len(data):
            raise ValueError('Question truncated')

        qtype, qclass = struct.unpack('>HH', data[offset:offset + 4])

        if qclass != codec.QCLASS_IN:
            raise ValueError('Unexpected class %d' % qclass)

        return query_id, qname, qtype

    def _send_response(self, query_id, qname, qtype, data, addr,
                       response_payload_cap, qname_wire_len, max_packet_size,
                       include_opt=True):
        """Build and send DNS response."""
        opt_record = self._opt_record if include_opt else b''
        opt_arcount = self._opt_arcount if include_opt else 0
        # Header
        flags = codec.FLAG_QR | codec.FLAG_AA  # Response + Authoritative
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            opt_arcount
        )

        # Question (echo back)
        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        # Answer
        answer = codec.encode_name(qname)
        if self._rtype != codec.QTYPE_CNAME:
            raise TransportError('Unsupported response type')

        try:
            cname_target = codec.encode_cname_target(
                data, self._cname_suffix, self._label_max_len
            )
        except ValueError as exc:
            log_event(
                self._logger,
                logging.WARNING,
                'dns.encode_error',
                'DNS response encode failed',
                lambda: {
                    'dns_id': query_id,
                    'qtype': qtype,
                    'payload_bytes': len(data),
                    'error': str(exc),
                },
            )
            raise TransportError('Invalid response data: %s' % exc)

        rdata = codec.encode_name(cname_target)

        answer += struct.pack('>HHIH',
            self._rtype,
            codec.QCLASS_IN,
            self._response_ttl,
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + opt_record
        response_len = len(response)
        oversize = False
        if max_packet_size is not None:
            oversize = response_len > max_packet_size

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)
        log_event(
            self._logger,
            logging.DEBUG,
            'dns.send',
            'DNS response sent',
            lambda: {
                'dns_id': query_id,
                'qtype': qtype,
                'rtype': self._rtype,
                'addr': '%s:%d' % (addr[0], addr[1]),
                'bytes': response_len,
                'payload_bytes': len(data),
                'response_payload_cap': response_payload_cap,
                'qname_wire_len': qname_wire_len,
                'rdata_len': len(rdata),
                'max_packet_size': max_packet_size,
                'oversize': oversize,
            },
        )

    def _send_empty_response(self, query_id, qname, qtype, addr, reason=None,
                             include_opt=True):
        """Send NOERROR response with no answers (NODATA) and SOA in authority."""
        opt_record = self._opt_record if include_opt else b''
        opt_arcount = self._opt_arcount if include_opt else 0
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            0,  # ANCOUNT
            1,  # NSCOUNT - SOA record for negative caching
            opt_arcount
        )

        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        # SOA record in authority section with TTL=0 to prevent negative caching
        response = header + question + self._soa_record + opt_record

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)
        log_event(
            self._logger,
            logging.DEBUG,
            'dns.send_empty',
            'DNS empty response sent',
            lambda: {
                'dns_id': query_id,
                'qtype': qtype,
                'addr': '%s:%d' % (addr[0], addr[1]),
                'bytes': len(response),
                'reason': reason,
            },
        )

    def _send_cname_followup(self, query_id, qname, qtype, addr):
        """Respond to resolver follow-up queries for CNAME targets."""
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            self._opt_arcount
        )

        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        addr_bytes = self._cname_a_addr_bytes

        answer = codec.encode_name(qname)
        rdata = codec.encode_a_rdata(addr_bytes)
        answer += struct.pack('>HHIH',
            codec.QTYPE_A,
            codec.QCLASS_IN,
            self._response_ttl,
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + self._opt_record

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)
        log_event(
            self._logger,
            logging.DEBUG,
            'dns.cname_followup',
            'DNS CNAME followup sent',
            lambda: {
                'dns_id': query_id,
                'qtype': qtype,
                'addr': '%s:%d' % (addr[0], addr[1]),
                'bytes': len(response),
            },
        )

    def _response_payload_cap(self, qname):
        if self._rtype != codec.QTYPE_CNAME:
            return None, None, None
        qname_wire_len = len(codec.encode_name(qname))
        payload_cap, max_packet_size = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            self._edns_size,
            self._cname_suffix,
            self._label_max_len,
            self._opt_record_len,
        )
        return payload_cap, qname_wire_len, max_packet_size

    def _compute_max_response_packet_mtu(self):
        max_query_payload = self._recv_packet_mtu
        min_query_payload = MIN_PACKET_MTU
        max_response_payload = 0
        for payload_len in range(min_query_payload, max_query_payload + 1):
            try:
                qname_wire_len = codec.calc_qname_wire_len(
                    payload_len,
                    self._base_domain,
                    self._label_max_len,
                )
            except ValueError:
                continue
            payload_cap, _ = codec.calc_cname_response_payload_cap(
                qname_wire_len,
                self._edns_size,
                self._cname_suffix,
                self._label_max_len,
                self._opt_record_len,
            )
            if payload_cap is not None and payload_cap > max_response_payload:
                max_response_payload = payload_cap
        return max_response_payload

    def _build_soa_record(self):
        """Build a minimal SOA record for authority section with TTL=0."""
        # SOA record for the base domain
        name = codec.encode_name(self._base_domain)
        # MNAME (primary NS) and RNAME (admin email) - use base domain
        mname = codec.encode_name('ns.' + self._base_domain)
        rname = codec.encode_name('hostmaster.' + self._base_domain)
        # SOA fields: SERIAL, REFRESH, RETRY, EXPIRE, MINIMUM (negative TTL)
        soa_data = mname + rname + struct.pack('>IIIII',
            1,  # SERIAL
            0,  # REFRESH
            0,  # RETRY
            0,  # EXPIRE
            0,  # MINIMUM (negative cache TTL = 0)
        )
        return name + struct.pack('>HHIH',
            codec.QTYPE_SOA,
            codec.QCLASS_IN,
            0,  # TTL
            len(soa_data)
        ) + soa_data

    def close(self):
        """Close the UDP socket."""
        if self._sock:
            self._sock.close()
            self._sock = None
