# -*- coding: ascii -*-
"""
DNS TXT server transport for Bob.

Receives tunnel packets from DNS TXT queries and sends TXT responses.
"""

from __future__ import absolute_import

import logging
import select
import socket
import struct

from ..transport_base import (
    Server,
    TransportError,
    raise_bind_error,
)
from ..mtu_limits import resolve_mtu_limits
from . import dns_txt_codec as codec
from ...config import Config
from ...logging_util import get_logger, log_event
from ...utils import parse_host_port
from ... import time_provider

_LOG = get_logger(__name__)

_QTYPE_SOA = 6


class _ResponseSender(object):
    def __init__(self, server, query_id, qname, qtype, addr,
                 response_payload_cap):
        self._server = server
        self._query_id = query_id
        self._qname = qname
        self._qtype = qtype
        self._addr = addr
        self.response_payload_cap = response_payload_cap

    def __call__(self, data):
        self._server._send_response(
            self._query_id,
            self._qname,
            self._qtype,
            data,
            self._addr,
            response_payload_cap=self.response_payload_cap,
        )


class DnsTxtServer(Server):
    """
    DNS TXT server transport for Bob.

    Listens for DNS TXT queries and responds with TXT records.
    """

    def __init__(self, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')

        self._config = config
        self._base_domain = config.dns_base_domain.lower().rstrip('.')
        self._label_max_len = config.dns_label_max_len
        self._response_ttl = int(config.dns_response_ttl)
        if self._response_ttl < 0:
            raise ValueError('dns_response_ttl must be >= 0')
        self._edns_size = config.dns_edns_size

        listen_addr = config.dns_listen_addr
        host, port = parse_host_port(listen_addr, default_port=53)
        self._listen_addr = (host, port)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind(self._listen_addr)
        except (socket.error, OSError) as exc:
            self._sock.close()
            self._sock = None
            raise_bind_error(exc, self._listen_addr, 'DNS TXT')

        if self._edns_size > 512:
            self._opt_record = codec.build_opt_record(self._edns_size)
            self._opt_arcount = 1
        else:
            self._opt_record = b''
            self._opt_arcount = 0

        self._recv_bufsize = max(self._edns_size,
                                 self._config.dns_recv_bufsize_min)
        self._soa_record = self._build_soa_record()

        send_mtu, recv_mtu, min_packet_mtu, mtu_constraints = resolve_mtu_limits(
            'dns_txt', config, role='server'
        )
        self._send_packet_mtu = send_mtu
        self._recv_packet_mtu = recv_mtu
        mtu_details = {
            'transport': 'dns_txt',
            'role': 'server',
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

        self._response_payload_cap = codec.calc_response_mtu(
            codec.QTYPE_TXT,
            self._edns_size,
        )

        if _LOG.isEnabledFor(logging.INFO):
            log_event(
                _LOG,
                logging.INFO,
                'dns_txt.server_config',
                'DNS TXT server config',
                lambda: {
                    'base_domain': self._base_domain,
                    'listen_addr': '%s:%d' % (self._listen_addr[0], self._listen_addr[1]),
                    'edns_size': self._edns_size,
                    'label_max_len': self._label_max_len,
                    'response_ttl': self._response_ttl,
                },
            )

    @property
    def recv_packet_mtu(self):
        return self._recv_packet_mtu

    @property
    def send_packet_mtu(self):
        return self._send_packet_mtu

    def recv(self, timeout=None):
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
                if _LOG.isEnabledFor(logging.DEBUG):
                    log_event(
                        _LOG,
                        logging.DEBUG,
                        'dns_txt.invalid_query',
                        'DNS TXT invalid query',
                        lambda: {'error': str(e)},
                    )
                continue

            qname_lower = qname.lower()
            is_our_domain = (qname_lower == self._base_domain or
                             qname_lower.endswith('.' + self._base_domain))
            if not is_our_domain:
                continue

            if qtype != codec.QTYPE_TXT:
                self._send_empty_response(
                    query_id,
                    qname,
                    qtype,
                    client_addr,
                    reason='qtype_mismatch',
                )
                continue

            try:
                data = codec.decode_query_name(
                    qname,
                    self._base_domain,
                    self._label_max_len,
                )
            except ValueError:
                self._send_empty_response(
                    query_id,
                    qname,
                    qtype,
                    client_addr,
                    reason='decode_failed',
                )
                continue

            response_payload_cap = self._response_payload_cap
            responder = _ResponseSender(
                self,
                query_id,
                qname,
                qtype,
                client_addr,
                response_payload_cap,
            )

            if _LOG.isEnabledFor(logging.DEBUG):
                log_event(
                    _LOG,
                    logging.DEBUG,
                    'dns_txt.recv',
                    'DNS TXT query received',
                    lambda: {
                        'dns_id': query_id,
                        'qtype': qtype,
                        'addr': '%s:%d' % (client_addr[0], client_addr[1]),
                        'query_bytes': len(pkt_data),
                        'bytes': len(data),
                        'response_payload_cap': response_payload_cap,
                    },
                )
            return data, responder

    def _parse_query(self, data):
        if len(data) < codec.DNS_HEADER_LEN:
            raise ValueError('Query too short')

        query_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            '>HHHHHH', data[:codec.DNS_HEADER_LEN]
        )

        if flags & codec.FLAG_QR:
            raise ValueError('Not a query')

        if qdcount < 1:
            raise ValueError('No question')

        qname, offset = codec.decode_name(
            data,
            codec.DNS_HEADER_LEN,
            allow_compression=True,
        )
        if offset + codec.DNS_QUESTION_FIXED_LEN > len(data):
            raise ValueError('Question truncated')

        qtype, qclass = struct.unpack(
            '>HH',
            data[offset:offset + codec.DNS_QUESTION_FIXED_LEN],
        )

        if qclass != codec.QCLASS_IN:
            raise ValueError('Unexpected class %d' % qclass)

        return query_id, qname, qtype

    def _send_response(self, query_id, qname, qtype, data, addr,
                       response_payload_cap, include_opt=True,
                       qname_wire=None):
        if response_payload_cap is not None and len(data) > response_payload_cap:
            if _LOG.isEnabledFor(logging.WARNING):
                log_event(
                    _LOG,
                    logging.WARNING,
                    'dns_txt.response_payload_oversize',
                    'DNS TXT response payload exceeds cap',
                    lambda: {
                        'dns_id': query_id,
                        'payload_bytes': len(data),
                        'response_payload_cap': response_payload_cap,
                    },
                )
            raise TransportError(
                'DNS TXT response payload %d exceeds cap %d' % (
                    len(data), response_payload_cap
                )
            )

        opt_record = self._opt_record if include_opt else b''
        opt_arcount = self._opt_arcount if include_opt else 0

        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            1,  # ANCOUNT
            0,  # NSCOUNT
            opt_arcount
        )

        if qname_wire is None:
            qname_wire = codec.encode_name(qname)
        question = qname_wire + struct.pack('>HH', qtype, codec.QCLASS_IN)

        answer = codec.build_compression_pointer(codec.DNS_HEADER_LEN)
        rdata = codec.encode_txt_rdata(data)
        answer += struct.pack('>HHIH',
            codec.QTYPE_TXT,
            codec.QCLASS_IN,
            self._response_ttl,
            len(rdata)
        )
        answer += rdata

        response = header + question + answer + opt_record
        response_len = len(response)
        max_packet_size = self._edns_size
        if response_len > max_packet_size:
            if _LOG.isEnabledFor(logging.ERROR):
                log_event(
                    _LOG,
                    logging.ERROR,
                    'dns_txt.response_oversize',
                    'DNS TXT response exceeds packet limit',
                    lambda: {
                        'dns_id': query_id,
                        'response_bytes': response_len,
                        'max_packet_size': max_packet_size,
                        'payload_bytes': len(data),
                        'rdata_len': len(rdata),
                    },
                )
            raise TransportError(
                'DNS TXT response size %d exceeds max %d' % (
                    response_len, max_packet_size
                )
            )

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)

        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns_txt.send',
                'DNS TXT response sent',
                lambda: {
                    'dns_id': query_id,
                    'qtype': qtype,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': response_len,
                    'payload_bytes': len(data),
                    'response_payload_cap': response_payload_cap,
                    'rdata_len': len(rdata),
                    'max_packet_size': max_packet_size,
                },
            )

    def _send_empty_response(self, query_id, qname, qtype, addr, reason=None,
                             include_opt=True):
        opt_record = self._opt_record if include_opt else b''
        opt_arcount = self._opt_arcount if include_opt else 0
        flags = codec.FLAG_QR | codec.FLAG_AA
        header = struct.pack('>HHHHHH',
            query_id,
            flags,
            1,  # QDCOUNT
            0,  # ANCOUNT
            1,  # NSCOUNT
            opt_arcount
        )

        question = codec.encode_name(qname)
        question += struct.pack('>HH', qtype, codec.QCLASS_IN)

        response = header + question + self._soa_record + opt_record

        try:
            self._sock.sendto(response, addr)
        except socket.error as e:
            raise TransportError('Send failed: %s' % e)
        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns_txt.send_empty',
                'DNS TXT empty response sent',
                lambda: {
                    'dns_id': query_id,
                    'qtype': qtype,
                    'addr': '%s:%d' % (addr[0], addr[1]),
                    'bytes': len(response),
                    'reason': reason,
                },
            )

    def _build_soa_record(self):
        name = codec.encode_name(self._base_domain)
        mname = codec.encode_name('ns.' + self._base_domain)
        rname = codec.encode_name('hostmaster.' + self._base_domain)
        soa_data = mname + rname + struct.pack('>IIIII',
            1,  # SERIAL
            0,  # REFRESH
            0,  # RETRY
            0,  # EXPIRE
            0,  # MINIMUM
        )
        return name + struct.pack('>HHIH',
            _QTYPE_SOA,
            codec.QCLASS_IN,
            0,
            len(soa_data)
        ) + soa_data

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None
