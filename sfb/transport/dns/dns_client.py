# -*- coding: ascii -*-
"""
DNS client transport for Alice.

Encodes tunnel packets into DNS A queries and decodes CNAME responses.
Supports pipelining with multiple in-flight queries.
"""

from __future__ import absolute_import

import os
import random
import select
import socket
import struct
import time

from ..transport_base import Transport, TransportError
from . import codec
from ...config import Config
from ...logging_util import get_logger


class _PendingQuery(object):
    """Tracks an in-flight DNS query."""

    __slots__ = ('dns_id', 'query_pkt', 'send_time')

    def __init__(self, dns_id, query_pkt):
        self.dns_id = dns_id
        self.query_pkt = query_pkt
        self.send_time = time.time()


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

        # Parse resolver address or use system resolver
        resolver = config.dns_resolver
        if resolver:
            if ':' in resolver:
                host, port = resolver.rsplit(':', 1)
                self._resolver = (host, int(port))
            else:
                self._resolver = (resolver, 53)
        else:
            resolvers = self._load_system_resolvers()
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
        self._recv_bufsize = max(self._edns_size, 4096)

        # Pending query tracking
        self._next_corr_id = 0
        self._pending = {}  # corr_id -> _PendingQuery
        self._dns_to_corr = {}  # dns_id -> corr_id

        # Rate limiting (token bucket)
        self._qps_limit = config.dns_queries_per_second
        self._tokens = self._qps_limit if self._qps_limit > 0 else 0
        self._last_refill = time.time()

    @property
    def send_mtu(self):
        return self._send_mtu

    @property
    def recv_mtu(self):
        return self._recv_mtu

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
        if self._qps_limit <= 0:
            return True  # Unlimited
        self._refill_tokens(time.time())
        return self._tokens >= 1.0

    def _refill_tokens(self, now):
        """Refill tokens based on elapsed time."""
        elapsed = now - self._last_refill
        self._tokens = min(self._qps_limit, self._tokens + elapsed * self._qps_limit)
        self._last_refill = now

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
        pending = _PendingQuery(dns_id, query_pkt)
        self._pending[corr_id] = pending
        self._dns_to_corr[dns_id] = corr_id

        # Consume a token for rate limiting
        if self._qps_limit > 0:
            self._tokens -= 1.0

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
        if timeout is None:
            # Block indefinitely until we get a valid response
            while True:
                try:
                    ready, _, _ = select.select([self._sock], [], [])
                except select.error as e:
                    raise TransportError('Select failed: %s' % e)
                if ready:
                    result = self._try_recv()
                    if result[0] is not None:
                        return result
        elif timeout == 0:
            # Non-blocking poll
            try:
                ready, _, _ = select.select([self._sock], [], [], 0)
            except select.error as e:
                raise TransportError('Select failed: %s' % e)
            if ready:
                return self._try_recv()
            return (None, None)
        else:
            # Wait up to timeout
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return (None, None)
                try:
                    ready, _, _ = select.select([self._sock], [], [], remaining)
                except select.error as e:
                    raise TransportError('Select failed: %s' % e)
                if not ready:
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
        except socket.error:
            return (None, None)

        result = self._parse_response(resp_data)
        if result is None:
            return (None, None)  # Malformed packet

        dns_id, payload = result

        if dns_id not in self._dns_to_corr:
            _LOG.debug('dns stale response dns_id=%d', dns_id)
            return (None, None)  # Stale or unknown query

        corr_id = self._dns_to_corr[dns_id]

        if payload is None:
            _LOG.debug('dns error response corr=%d dns_id=%d', corr_id, dns_id)
            return (None, None)  # RCODE error, drop

        # Clean up tracking
        del self._pending[corr_id]
        del self._dns_to_corr[dns_id]

        _LOG.debug('dns recv corr=%d dns_id=%d len=%d', corr_id, dns_id, len(payload))
        return (corr_id, payload)

    def _prune_stale(self, now=None):
        """Remove stale pending queries to free capacity."""
        if now is None:
            now = time.time()
        stale = [cid for cid, pq in self._pending.items()
                 if now - pq.send_time > self._pending_timeout]
        for cid in stale:
            dns_id = self._pending[cid].dns_id
            del self._pending[cid]
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
            tuple: (query_id, payload_bytes) on success
            tuple: (query_id, None) if RCODE indicates error
            None: if packet is malformed
        """
        if len(data) < 12:
            return None

        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:12]
        )

        if not (flags & codec.FLAG_QR):
            return query_id, None  # Not a response

        # Check RCODE
        rcode = flags & codec.RCODE_MASK
        if rcode != codec.RCODE_NOERROR:
            _LOG.debug('dns rcode=%d id=%d', rcode, query_id)
            return query_id, None

        # Skip questions
        offset = 12
        try:
            for _ in range(qdcount):
                offset = codec.skip_name(data, offset)
                offset += 4  # QTYPE + QCLASS
        except ValueError:
            return query_id, None

        if ancount < 1:
            return query_id, None

        for _ in range(ancount):
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

            if offset + rdlength > len(data):
                return query_id, None

            if rclass != codec.QCLASS_IN or rtype != self._rtype:
                offset += rdlength
                continue

            if rtype != codec.QTYPE_CNAME:
                offset += rdlength
                continue

            try:
                cname, end_offset = codec.decode_name(
                    data, offset, allow_compression=True
                )
            except ValueError:
                _LOG.debug('dns invalid cname id=%d', query_id)
                return query_id, None

            if end_offset > offset + rdlength:
                _LOG.debug('dns cname exceeds rdlength id=%d', query_id)
                return query_id, None

            try:
                payload = codec.decode_cname_target(
                    cname, self._cname_suffix, self._label_max_len
                )
            except ValueError:
                _LOG.debug('dns invalid cname payload id=%d', query_id)
                return query_id, None

            return query_id, payload

        return query_id, None

    def close(self):
        """Close the UDP socket and cancel all pending queries."""
        self._pending.clear()
        self._dns_to_corr.clear()
        if self._sock:
            self._sock.close()
            self._sock = None

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
