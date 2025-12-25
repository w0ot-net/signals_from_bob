# -*- coding: ascii -*-
"""
DNS client transport for Alice.

Encodes tunnel packets into DNS TXT queries and decodes responses.
"""

from __future__ import absolute_import

import base64
import random
import socket
import struct

from ..transport_base import ClientTransport, TransportError


# DNS constants
QTYPE_TXT = 16
QTYPE_NULL = 10
QCLASS_IN = 1

# DNS header flags
FLAG_RD = 0x0100  # Recursion Desired
FLAG_QR = 0x8000  # Response

# Limits
MAX_LABEL_LEN = 63
MAX_NAME_LEN = 253
NONCE_LEN = 4


class DnsClient(ClientTransport):
    """
    DNS client transport for Alice.

    Sends tunnel packets as DNS TXT queries and receives responses.
    Supports direct mode (query specific server) and resolver mode
    (use system DNS).
    """

    def __init__(self, base_domain, resolver=None, timeout=5.0, qtype=QTYPE_TXT):
        """
        Initialize DNS client transport.

        Args:
            base_domain: Tunnel domain suffix (e.g., 'tunnel.example.com')
            resolver: DNS server as 'host:port' or 'host' (default: system DNS)
            timeout: Query timeout in seconds
            qtype: Query type (QTYPE_TXT or QTYPE_NULL)
        """
        self._base_domain = base_domain.lower().rstrip('.')
        self._timeout = timeout
        self._qtype = qtype
        self._nonce = random.randint(0, 0xFFFF)
        self._query_id = random.randint(0, 0xFFFF)

        # Parse resolver address
        if resolver:
            if ':' in resolver:
                host, port = resolver.rsplit(':', 1)
                self._resolver = (host, int(port))
            else:
                self._resolver = (resolver, 53)
        else:
            self._resolver = None  # Use system resolver

        # Create UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)

        # Calculate MTUs
        self._send_mtu = self._calc_query_mtu()
        self._recv_mtu = self._calc_response_mtu()

    def _calc_query_mtu(self):
        """Calculate max bytes that can be sent in a query."""
        # available = 253 - len(base_domain) - 1 (trailing dot) - 5 (nonce+dot)
        available = MAX_NAME_LEN - len(self._base_domain) - 1 - NONCE_LEN - 1
        # Account for dots between labels
        label_overhead = available // (MAX_LABEL_LEN + 1)
        usable = available - label_overhead
        # Base32: 8 bytes -> 13 chars, so chars * 5 / 8
        return (usable * 5) // 8

    def _calc_response_mtu(self):
        """Calculate max bytes that can be received in a response."""
        # Standard TXT: 255 chars base64 -> 191 bytes
        # Base64: 4 chars -> 3 bytes
        return (255 * 3) // 4

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

    def exchange(self, data):
        """
        Send data in DNS query, return response data.

        Args:
            data: bytes to send

        Returns:
            bytes: response data

        Raises:
            TransportError: on I/O or protocol error
        """
        if len(data) > self._send_mtu:
            raise TransportError(
                'Data size %d exceeds send MTU %d' % (len(data), self._send_mtu)
            )

        # Build query
        query_name = self._encode_query(data)
        query_id = self._next_query_id()
        query_pkt = self._build_query(query_id, query_name)

        # Send query
        try:
            if self._resolver:
                self._sock.sendto(query_pkt, self._resolver)
            else:
                raise TransportError('System resolver not implemented')
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        # Receive response
        try:
            while True:
                resp_data, addr = self._sock.recvfrom(4096)
                resp_id, resp_payload = self._parse_response(resp_data)
                if resp_id == query_id:
                    return resp_payload
                # Ignore mismatched query IDs (stale responses)
        except socket.timeout:
            raise TransportError('Query timeout')
        except socket.error as e:
            raise TransportError('Receive failed: %s' % e)

    def _encode_query(self, data):
        """Encode data into DNS query name with nonce."""
        # Generate nonce
        nonce = _base32_encode(struct.pack('>H', self._nonce))[:NONCE_LEN]
        self._nonce = (self._nonce + 1) & 0xFFFF

        # Base32 encode data
        b32 = _base32_encode(data)

        # Split into labels
        labels = [nonce]
        while b32:
            labels.append(b32[:MAX_LABEL_LEN])
            b32 = b32[MAX_LABEL_LEN:]

        # Append base domain
        labels.extend(self._base_domain.split('.'))
        return '.'.join(labels)

    def _next_query_id(self):
        """Generate next query ID."""
        qid = self._query_id
        self._query_id = (self._query_id + 1) & 0xFFFF
        return qid

    def _build_query(self, query_id, name):
        """Build DNS query packet."""
        # Header: ID, FLAGS, QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
        header = struct.pack('>HHHHHH',
            query_id,
            FLAG_RD,  # Recursion Desired
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0   # ARCOUNT
        )

        # Question: QNAME, QTYPE, QCLASS
        qname = _encode_dns_name(name)
        question = qname + struct.pack('>HH', self._qtype, QCLASS_IN)

        return header + question

    def _parse_response(self, data):
        """
        Parse DNS response packet.

        Returns:
            tuple: (query_id, payload_bytes)

        Raises:
            TransportError: on parse error
        """
        if len(data) < 12:
            raise TransportError('Response too short')

        # Parse header
        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:12]
        )

        if not (flags & FLAG_QR):
            raise TransportError('Not a response')

        # Skip questions
        offset = 12
        for _ in range(qdcount):
            offset = _skip_dns_name(data, offset)
            offset += 4  # QTYPE + QCLASS

        # Parse first answer
        if ancount < 1:
            raise TransportError('No answer in response')

        offset = _skip_dns_name(data, offset)  # NAME

        if offset + 10 > len(data):
            raise TransportError('Answer too short')

        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', data[offset:offset + 10]
        )
        offset += 10

        if offset + rdlength > len(data):
            raise TransportError('RDATA truncated')

        rdata = data[offset:offset + rdlength]

        # Decode based on record type
        if rtype == QTYPE_TXT:
            payload = self._decode_txt_rdata(rdata)
        elif rtype == QTYPE_NULL:
            payload = _base64_decode(rdata.decode('ascii'))
        else:
            raise TransportError('Unexpected record type %d' % rtype)

        return query_id, payload

    def _decode_txt_rdata(self, rdata):
        """Decode TXT record RDATA (concatenate strings, base64 decode)."""
        strings = []
        offset = 0
        while offset < len(rdata):
            length = rdata[offset] if isinstance(rdata[offset], int) else ord(rdata[offset])
            offset += 1
            if offset + length > len(rdata):
                raise TransportError('TXT string truncated')
            strings.append(rdata[offset:offset + length])
            offset += length

        b64 = b''.join(strings).decode('ascii')
        return _base64_decode(b64)

    def close(self):
        """Close the UDP socket."""
        if self._sock:
            self._sock.close()
            self._sock = None


def _base32_encode(data):
    """Encode bytes to base32 without padding, uppercase."""
    return base64.b32encode(data).rstrip(b'=').decode('ascii')


def _base32_decode(s):
    """Decode base32 string to bytes (handles missing padding)."""
    # Add padding
    pad = (8 - len(s) % 8) % 8
    s = s.upper() + '=' * pad
    return base64.b32decode(s)


def _base64_encode(data):
    """Encode bytes to base64 without padding."""
    return base64.b64encode(data).rstrip(b'=').decode('ascii')


def _base64_decode(s):
    """Decode base64 string to bytes (handles missing padding)."""
    # Add padding
    pad = (4 - len(s) % 4) % 4
    s = s + '=' * pad
    return base64.b64decode(s)


def _encode_dns_name(name):
    """Encode domain name to DNS wire format."""
    parts = []
    for label in name.split('.'):
        if label:
            encoded = label.encode('ascii')
            parts.append(struct.pack('B', len(encoded)) + encoded)
    parts.append(b'\x00')  # Root label
    return b''.join(parts)


def _skip_dns_name(data, offset):
    """Skip a DNS name in wire format, return new offset."""
    while offset < len(data):
        length = data[offset] if isinstance(data[offset], int) else ord(data[offset])
        if length == 0:
            return offset + 1
        if (length & 0xC0) == 0xC0:
            # Compression pointer
            return offset + 2
        offset += 1 + length
    raise TransportError('Invalid DNS name')
