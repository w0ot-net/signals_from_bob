# -*- coding: ascii -*-
"""
DNS client transport for Alice.

Encodes tunnel packets into DNS TXT queries and decodes responses.
"""

from __future__ import absolute_import

import os
import random
import socket
import struct

from ..transport_base import RequestResponseTransport, TransportError
from . import codec
from ...logging_util import get_logger


class DnsClient(RequestResponseTransport):
    """
    DNS client transport for Alice.

    Sends tunnel packets as DNS TXT queries and receives responses.
    Supports direct mode (query specific server) and resolver mode
    (use system DNS).
    """

    def __init__(self, base_domain, resolver=None, timeout=5.0,
                 qtype=codec.QTYPE_TXT, edns_size=512):
        """
        Initialize DNS client transport.

        Args:
            base_domain: Tunnel domain suffix (e.g., 'tunnel.example.com')
            resolver: DNS server as 'host:port' or 'host' (default: system DNS)
            timeout: Query timeout in seconds
            qtype: Query type (QTYPE_TXT or QTYPE_NULL)
            edns_size: EDNS0 UDP buffer size (512=standard, 4096=large)
        """
        self._base_domain = base_domain.lower().rstrip('.')
        self._timeout = timeout
        self._qtype = qtype
        self._edns_size = edns_size
        self._nonce = random.randint(0, 0xFFFF)
        self._query_id = random.randint(0, 0xFFFF)

        # Parse resolver address or use system resolver
        if resolver:
            if ':' in resolver:
                host, port = resolver.rsplit(':', 1)
                self._resolvers = [(host, int(port))]
            else:
                self._resolvers = [(resolver, 53)]
        else:
            self._resolvers = self._load_system_resolvers()
            if not self._resolvers:
                raise TransportError('No system resolvers found')
        self._resolver_index = 0

        # Create UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)

        # Calculate MTUs
        self._send_mtu = codec.calc_query_mtu(self._base_domain)
        self._recv_mtu = codec.calc_response_mtu(edns_size)
        self._recv_bufsize = max(self._edns_size, 4096)

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
        self._resolver_index = 0

        # Build query
        query_name = self._encode_query(data)
        query_id = self._next_query_id()
        query_pkt = self._build_query(query_id, query_name)

        for _ in range(len(self._resolvers)):
            # Send query
            try:
                _LOG.debug('dns send id=%d resolver=%s', query_id,
                           self._resolvers[self._resolver_index])
                self._send_query(query_pkt)
            except socket.error as e:
                raise TransportError('Send failed: %s' % e)

            # Receive response
            try:
                while True:
                    resp_data, addr = self._sock.recvfrom(self._recv_bufsize)
                    result = self._parse_response(resp_data)

                    if result is None:
                        # Malformed packet, ignore
                        continue

                    resp_id, resp_payload = result

                    if resp_id != query_id:
                        # Wrong query ID (stale response), ignore
                        continue

                    if resp_payload is None:
                        # RCODE error or no answer - keep waiting, let timeout
                        # trigger reliability layer retransmit
                        continue

                    return resp_payload
            except socket.timeout:
                _LOG.debug('dns timeout id=%d resolver=%s', query_id,
                           self._resolvers[self._resolver_index])
                if not self._advance_resolver():
                    break
            except socket.error as e:
                raise TransportError('Receive failed: %s' % e)

        raise TransportError('Query timeout')

    def _encode_query(self, data):
        """Encode data into DNS query name with nonce."""
        nonce = self._nonce
        self._nonce = (self._nonce + 1) & 0xFFFF
        return codec.encode_query_name(data, self._base_domain, nonce)

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
            tuple: (query_id, payload_bytes) on success
            tuple: (query_id, None) if RCODE indicates error (drop, let reliability retry)
            None: if packet is too short to read header

        Raises:
            TransportError: on parse error
        """
        if len(data) < 12:
            return None  # Malformed, ignore

        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:12]
        )

        if not (flags & codec.FLAG_QR):
            return query_id, None  # Not a response, drop

        # Check RCODE - if not NOERROR, drop and let reliability retry
        rcode = flags & codec.RCODE_MASK
        if rcode != codec.RCODE_NOERROR:
            _LOG.debug('dns rcode=%d id=%d', rcode, query_id)
            return query_id, None  # Error response, drop

        # Skip questions
        offset = 12
        try:
            for _ in range(qdcount):
                offset = codec.skip_name(data, offset)
                offset += 4  # QTYPE + QCLASS
        except ValueError:
            return query_id, None

        if ancount < 1:
            return query_id, None  # No answer, drop

        try:
            offset = codec.skip_name(data, offset)  # NAME
        except ValueError:
            return query_id, None

        if offset + 10 > len(data):
            return query_id, None

        rtype, rclass, ttl, rdlength = struct.unpack(
            '>HHIH', data[offset:offset + 10]
        )
        offset += 10

        if rclass != codec.QCLASS_IN:
            return query_id, None

        if offset + rdlength > len(data):
            return query_id, None

        rdata = data[offset:offset + rdlength]

        if rtype == codec.QTYPE_TXT:
            try:
                payload = codec.decode_txt_rdata(rdata)
            except ValueError:
                return query_id, None
        elif rtype == codec.QTYPE_NULL:
            try:
                payload = codec.base64_decode(rdata.decode('ascii'))
            except (UnicodeDecodeError, ValueError):
                _LOG.debug('dns invalid null payload id=%d', query_id)
                return query_id, None
        else:
            return query_id, None

        return query_id, payload

    def close(self):
        """Close the UDP socket."""
        if self._sock:
            self._sock.close()
            self._sock = None

    def _send_query(self, query_pkt):
        self._sock.sendto(query_pkt, self._resolvers[self._resolver_index])

    def _advance_resolver(self):
        if self._resolver_index + 1 >= len(self._resolvers):
            return False
        self._resolver_index += 1
        return True

    def _load_system_resolvers(self):
        if os.name == 'nt':
            return self._load_windows_resolvers()
        resolvers = []
        try:
            handle = open('/etc/resolv.conf', 'r')
        except (IOError, OSError):
            return []
        with handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if parts[0] != 'nameserver' or len(parts) < 2:
                    continue
                host = parts[1]
                for addr in self._resolve_host(host, 53):
                    if addr not in resolvers:
                        resolvers.append(addr)
        return resolvers

    def _load_windows_resolvers(self):
        resolvers = []
        try:
            try:
                import winreg
            except ImportError:
                import _winreg as winreg
        except ImportError:
            return []

        values = []
        base_path = r'SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters'
        values.extend(self._read_registry_nameservers(winreg, base_path))
        interfaces = base_path + r'\\Interfaces'
        for subkey in self._enum_registry_keys(winreg, interfaces):
            values.extend(self._read_registry_nameservers(
                winreg, interfaces + r'\\' + subkey
            ))

        for host in self._split_nameserver_values(values):
            for addr in self._resolve_host(host, 53):
                if addr not in resolvers:
                    resolvers.append(addr)

        return resolvers

    def _read_registry_nameservers(self, winreg, path):
        values = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return values
        try:
            for name in ('NameServer', 'DhcpNameServer'):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if value:
                    values.append(value)
        finally:
            winreg.CloseKey(key)
        return values

    def _enum_registry_keys(self, winreg, path):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return []
        names = []
        index = 0
        try:
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                names.append(name)
                index += 1
        finally:
            winreg.CloseKey(key)
        return names

    def _split_nameserver_values(self, values):
        hosts = []
        for value in values:
            try:
                text = value.strip()
            except AttributeError:
                continue
            if not text:
                continue
            text = text.replace(',', ' ')
            for host in text.split():
                if host and host not in hosts:
                    hosts.append(host)
        return hosts

    def _resolve_host(self, host, port):
        addrs = []
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET,
                                       socket.SOCK_DGRAM)
        except socket.gaierror:
            return []
        for family, socktype, proto, canonname, addr in infos:
            if addr not in addrs:
                addrs.append(addr)
        return addrs


_LOG = get_logger(__name__)
