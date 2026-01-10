# -*- coding: ascii -*-
"""
DNS flat stager helper for Bob.
"""

from __future__ import absolute_import

import logging
import struct

from . import dns_codec as codec
from ...config import DNS_STANDARD_SIZE
from ...logging_util import log_event


class DnsFlatStager(object):
    def __init__(self, base_domain, flat_chunks, flat_count, flat_meta,
                 flat_chunk_size, rtype, cname_suffix, label_max_len, logger,
                 send_response, send_empty_response):
        self._base_domain = base_domain
        self._flat_chunks = flat_chunks
        if self._flat_chunks:
            if flat_count is None:
                flat_count = len(self._flat_chunks)
            if flat_count > len(self._flat_chunks):
                flat_count = len(self._flat_chunks)
        else:
            flat_count = 0
        self._flat_count = flat_count
        self._flat_meta = flat_meta
        self._flat_chunk_size = flat_chunk_size
        self._rtype = rtype
        self._cname_suffix = cname_suffix
        self._label_max_len = label_max_len
        self._logger = logger
        self._send_response = send_response
        self._send_empty_response = send_empty_response
        self._enabled = bool(self._flat_chunks and self._flat_count)
        if self._enabled and not self._flat_meta:
            self._flat_meta = struct.pack('>2sBI', b'SF', 1, self._flat_count)
        if self._enabled:
            self._flat_count_name = 'flat0.count.%s' % self._base_domain
            self._flat_piece_prefix = 'flat0.'
            self._flat_piece_suffix = '.%s' % self._base_domain
        else:
            self._flat_count_name = None
            self._flat_piece_prefix = None
            self._flat_piece_suffix = None

    @property
    def enabled(self):
        return self._enabled

    def handle_query(self, query_id, qname, qname_lower, qtype, addr):
        if not self._enabled:
            return False
        name = qname_lower.rstrip('.')
        if name == self._flat_count_name:
            if not self._flat_meta:
                self._send_empty_response(
                    query_id, qname, qtype, addr,
                    reason='flat_missing',
                    include_opt=False,
                )
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'dns.flat_invalid',
                    'DNS flat stager count missing',
                    lambda: {'dns_id': query_id},
                )
                return True
            self._send_stager_response(query_id, qname, qtype, self._flat_meta, addr)
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_count',
                'DNS flat stager count sent',
                lambda: {
                    'dns_id': query_id,
                    'count': self._flat_count,
                    'bytes': len(self._flat_meta),
                },
            )
            return True
        if not name.startswith(self._flat_piece_prefix):
            return False
        if not name.endswith(self._flat_piece_suffix):
            return False
        index_text = name[len(self._flat_piece_prefix):-len(self._flat_piece_suffix)]
        if len(index_text) != 5 or not index_text.isdigit():
            self._send_empty_response(
                query_id, qname, qtype, addr,
                reason='flat_invalid',
                include_opt=False,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_invalid',
                'DNS flat stager invalid index',
                lambda: {'dns_id': query_id, 'index': index_text},
            )
            return True
        index = int(index_text)
        if index < 1 or index > self._flat_count:
            self._send_empty_response(
                query_id, qname, qtype, addr,
                reason='flat_invalid',
                include_opt=False,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_invalid',
                'DNS flat stager index out of range',
                lambda: {'dns_id': query_id, 'index': index},
            )
            return True
        payload = self._flat_chunks[index - 1]
        self._send_stager_response(query_id, qname, qtype, payload, addr)
        log_event(
            self._logger,
            logging.DEBUG,
            'dns.flat_piece',
            'DNS flat stager piece sent',
            lambda: {
                'dns_id': query_id,
                'index': index,
                'count': self._flat_count,
                'bytes': len(payload),
            },
        )
        return True

    def _stager_response_payload_cap(self, qname):
        if self._rtype != codec.QTYPE_CNAME:
            return None, None, None
        qname_wire_len = len(codec.encode_name(qname))
        payload_cap, max_packet_size = codec.calc_cname_response_payload_cap(
            qname_wire_len,
            DNS_STANDARD_SIZE,
            self._cname_suffix,
            self._label_max_len,
            0,
        )
        return payload_cap, qname_wire_len, max_packet_size

    def _send_stager_response(self, query_id, qname, qtype, data, addr):
        response_payload_cap, qname_wire_len, max_packet_size = (
            self._stager_response_payload_cap(qname)
        )
        self._send_response(
            query_id,
            qname,
            qtype,
            data,
            addr,
            response_payload_cap=response_payload_cap,
            qname_wire_len=qname_wire_len,
            max_packet_size=max_packet_size,
            include_opt=False,
        )
