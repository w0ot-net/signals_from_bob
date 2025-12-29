# -*- coding: ascii -*-
"""
DNS server transport for Bob.

Receives tunnel packets from DNS A queries and sends CNAME responses.
"""

from __future__ import absolute_import

import select
import socket
import struct

from ..transport_base import Server, TransportError
from . import codec
from ...config import Config
from ...logging_util import get_logger


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
        self._cname_a_addr = config.dns_cname_a_addr

        # Parse listen address
        listen_addr = config.dns_listen_addr
        if ':' in listen_addr:
            host, port = listen_addr.rsplit(':', 1)
            self._listen_addr = (host, int(port))
        else:
            self._listen_addr = (listen_addr, 53)

        # Create and bind UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(self._listen_addr)
        except Exception:
            self._sock.close()
            self._sock = None
            raise

        # Calculate MTUs
        self._recv_mtu = codec.calc_query_mtu(self._base_domain,
                                              self._label_max_len)
        self._send_mtu = codec.calc_response_mtu(self._rtype,
                                                 config.dns_edns_size,
                                                 self._cname_suffix,
                                                 self._label_max_len)
        self._logger = get_logger(__name__)
        self._logger.debug(
            'DnsServer MTU calc: base_domain=%r label_max=%d recv_mtu=%d send_mtu=%d',
            self._base_domain, self._label_max_len, self._recv_mtu, self._send_mtu
        )

    @property
    def recv_mtu(self):
        return self._recv_mtu

    @property
    def send_mtu(self):
        return self._send_mtu

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
        while True:
            try:
                if timeout is None:
                    ready, _, _ = select.select([self._sock], [], [])
                else:
                    ready, _, _ = select.select([self._sock], [], [], timeout)
                if not ready:
                    return None, None
                pkt_data, client_addr = self._sock.recvfrom(max(self._edns_size, 4096))
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            except socket.error as e:
                raise TransportError('Receive failed: %s' % e)

            try:
                query_id, qname, qtype = self._parse_query(pkt_data)
            except (ValueError, TransportError) as e:
                # Malformed query, ignore
                self._logger.debug('dns invalid query: %s', e)
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
                self._send_empty_response(query_id, qname, qtype, client_addr)
                continue

            cname_suffix = self._cname_suffix.lower()
            if (qname_lower == cname_suffix or
                    qname_lower.endswith('.' + cname_suffix)):
                self._send_cname_followup(query_id, qname, qtype, client_addr)
                continue

            # Decode tunnel data
            try:
                data = codec.decode_query_name(qname, self._base_domain,
                                               self._label_max_len)
            except ValueError:
                # Decode failed, send empty response to avoid resolver timeouts
                self._send_empty_response(query_id, qname, qtype, client_addr)
                continue

            # Create responder
            def responder(response_data, _qid=query_id, _qname=qname,
                          _qtype=qtype, _addr=client_addr):
                self._send_response(_qid, _qname, _qtype, response_data, _addr)

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

    def _send_response(self, query_id, qname, qtype, data, addr):
        """Build and send DNS response."""
        # Include OPT record for EDNS0 if enabled
        if self._edns_size > 512:
            arcount = 1
            additional = codec.build_opt_record(self._edns_size)
        else:
            arcount = 0
            additional = b''

        # Header
        flags = codec.FLAG_QR | codec.FLAG_AA  # Response + Authoritative
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            arcount
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
            raise TransportError('Invalid response data: %s' % exc)

        rdata = codec.encode_name(cname_target)

        answer += struct.pack('>HHIH',
            self._rtype,
            codec.QCLASS_IN,
            0,  # TTL
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + additional

        try:
            self._logger.debug('dns response id=%d addr=%s', query_id, addr)
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

    def _send_empty_response(self, query_id, qname, qtype, addr):
        """Send NOERROR response with no answers (NODATA) and SOA in authority."""
        if self._edns_size > 512:
            arcount = 1
            additional = codec.build_opt_record(self._edns_size)
        else:
            arcount = 0
            additional = b''

        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            0,  # ANCOUNT
            1,  # NSCOUNT - SOA record for negative caching
            arcount
        )

        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        # SOA record in authority section with TTL=0 to prevent negative caching
        authority = self._build_soa_record()

        response = header + question + authority + additional

        try:
            self._logger.debug('dns empty response id=%d addr=%s', query_id, addr)
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

    def _send_cname_followup(self, query_id, qname, qtype, addr):
        """Respond to resolver follow-up queries for CNAME targets."""
        if self._edns_size > 512:
            arcount = 1
            additional = codec.build_opt_record(self._edns_size)
        else:
            arcount = 0
            additional = b''

        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            arcount
        )

        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        try:
            addr_bytes = socket.inet_aton(self._cname_a_addr)
        except (socket.error, OSError):
            addr_bytes = b'\x00\x00\x00\x00'

        answer = codec.encode_name(qname)
        rdata = codec.encode_a_rdata(addr_bytes)
        answer += struct.pack('>HHIH',
            codec.QTYPE_A,
            codec.QCLASS_IN,
            0,  # TTL
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + additional

        try:
            self._logger.debug('dns cname followup id=%d addr=%s', query_id, addr)
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

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
