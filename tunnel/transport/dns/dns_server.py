# -*- coding: ascii -*-
"""
DNS server transport for Bob.

Receives tunnel packets from DNS TXT queries and sends responses.
"""

from __future__ import absolute_import

import socket
import struct

from ..transport_base import RequestResponseServer, TransportError
from . import codec


class DnsServer(RequestResponseServer):
    """
    DNS server transport for Bob.

    Listens for DNS TXT queries and responds with TXT records.
    """

    def __init__(self, base_domain, listen_addr='0.0.0.0:53',
                 rtype=codec.QTYPE_TXT, edns_size=512):
        """
        Initialize DNS server transport.

        Args:
            base_domain: Tunnel domain suffix to recognize
            listen_addr: Address to listen on as 'host:port' or 'host'
            rtype: Response record type (QTYPE_TXT or QTYPE_NULL)
            edns_size: EDNS0 UDP buffer size for responses
        """
        self._base_domain = base_domain.lower().rstrip('.')
        self._rtype = rtype
        self._edns_size = edns_size

        # Parse listen address
        if ':' in listen_addr:
            host, port = listen_addr.rsplit(':', 1)
            self._listen_addr = (host, int(port))
        else:
            self._listen_addr = (listen_addr, 53)

        # Create and bind UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self._listen_addr)

        # Calculate MTUs
        self._recv_mtu = codec.calc_query_mtu(self._base_domain)
        self._send_mtu = codec.calc_response_mtu(edns_size)

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
        self._sock.settimeout(timeout)

        while True:
            try:
                pkt_data, client_addr = self._sock.recvfrom(max(self._edns_size, 4096))
            except socket.timeout:
                return None, None
            except socket.error as e:
                raise TransportError('Receive failed: %s' % e)

            try:
                query_id, qname, qtype = self._parse_query(pkt_data)
            except (ValueError, TransportError):
                # Malformed query, ignore
                continue

            # Check if it's for our domain
            if not qname.lower().endswith('.' + self._base_domain):
                # Not our query, ignore
                continue

            # Check query type
            if qtype not in (codec.QTYPE_TXT, codec.QTYPE_NULL):
                # Not a tunnel query, ignore
                continue

            # Decode tunnel data
            try:
                data = codec.decode_query_name(qname, self._base_domain)
            except ValueError:
                # Decode failed, ignore
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
        qname, offset = codec.decode_name(data, 12, allow_compression=False)

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
        if self._rtype == codec.QTYPE_TXT:
            rdata = codec.encode_txt_rdata(data)
        else:
            # NULL record: raw base64
            rdata = codec.base64_encode(data).encode('ascii')

        answer += struct.pack('>HHIH',
            self._rtype,
            codec.QCLASS_IN,
            0,  # TTL
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + additional

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

    def close(self):
        """Close the UDP socket."""
        if self._sock:
            self._sock.close()
            self._sock = None
