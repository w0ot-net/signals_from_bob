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
from ...protocol.constants import MIN_PACKET_MTU
from ..transport_base import TransportFatalError


_CACHE_BUSTER_PREFIX = 'r-'
_INDEX_TOKEN_HEX_LEN = 8
_INDEX_TOKEN_MASK = 0xFFFFFFFF


class DnsFlatStager(object):
    def __init__(self, base_domain, flat_chunks, flat_count, flat_meta,
                 flat_chunk_size, index_seed, rtype, cname_suffix, label_max_len,
                 logger, send_response, send_empty_response):
        self._base_domain = base_domain
        self._base_suffix = '.%s' % self._base_domain
        self._flat_chunks = flat_chunks
        requested_count = flat_count
        clamped = False
        if self._flat_chunks:
            if flat_count is None:
                flat_count = len(self._flat_chunks)
            if flat_count > len(self._flat_chunks):
                flat_count = len(self._flat_chunks)
                clamped = True
        else:
            flat_count = 0
        self._flat_count = flat_count
        self._flat_meta = flat_meta
        self._flat_chunk_size = flat_chunk_size
        seed_value, seed_hex, seed_reason = self._parse_index_seed(index_seed)
        self._index_seed = seed_value
        self._index_seed_hex = seed_hex
        self._index_seed_reason = seed_reason
        self._rtype = rtype
        self._cname_suffix = cname_suffix
        self._label_max_len = label_max_len
        self._logger = logger
        self._send_response = send_response
        self._send_empty_response = send_empty_response
        self._enabled = bool(
            self._flat_chunks and self._flat_count and self._index_seed_reason is None
        )
        if self._enabled:
            meta_count = self._parse_flat_meta_count(self._flat_meta)
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_stager_init',
                'DNS flat stager initialized',
                lambda: {
                    'requested_count': requested_count,
                    'flat_count': self._flat_count,
                    'meta_count': meta_count,
                    'meta_bytes': len(self._flat_meta) if self._flat_meta else 0,
                    'clamped': clamped,
                    'chunks': len(self._flat_chunks),
                    'chunk_size': self._flat_chunk_size,
                },
            )
            if self._flat_meta and meta_count != self._flat_count:
                self._flat_meta = struct.pack(
                    '>2sBI', b'SF', 1, self._flat_count
                )
                log_event(
                    self._logger,
                    logging.DEBUG,
                    'dns.flat_meta_rebuilt',
                    'DNS flat stager meta rebuilt',
                    lambda: {
                        'requested_count': requested_count,
                        'flat_count': self._flat_count,
                        'meta_count': meta_count,
                        'clamped': clamped,
                    },
                )
            elif not self._flat_meta:
                self._flat_meta = struct.pack(
                    '>2sBI', b'SF', 1, self._flat_count
                )

    @staticmethod
    def _parse_flat_meta_count(meta):
        if not meta or len(meta) < 7:
            return None
        try:
            header, version, count = struct.unpack('>2sBI', meta[:7])
        except (struct.error, TypeError):
            return None
        if header != b'SF' or version != 1:
            return None
        return count

    @staticmethod
    def _is_base32_label(label):
        allowed = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')
        try:
            text = label.upper()
        except AttributeError:
            return False
        for ch in text:
            if ch not in allowed:
                return False
        return True

    @staticmethod
    def _parse_index_seed(seed):
        if seed is None:
            return None, None, 'seed_missing'
        try:
            seed_value = int(seed)
        except (TypeError, ValueError):
            return None, None, 'seed_invalid'
        if seed_value < 0 or seed_value > _INDEX_TOKEN_MASK:
            return None, None, 'seed_invalid'
        return seed_value, '%08x' % seed_value, None

    @staticmethod
    def _is_hex_token(label):
        if not label or len(label) != _INDEX_TOKEN_HEX_LEN:
            return False
        for ch in label:
            if ch not in '0123456789abcdef':
                return False
        return True

    @property
    def enabled(self):
        return self._enabled

    @property
    def seed_hex(self):
        return self._index_seed_hex

    @property
    def seed_reason(self):
        return self._index_seed_reason

    def handle_query(self, query_id, qname, qname_lower, qtype, addr):
        if not self._enabled:
            return False
        name = qname_lower.rstrip('.')
        if name == self._base_domain:
            return False
        if not name.endswith(self._base_suffix):
            return False
        prefix = name[:-len(self._base_suffix)]
        if not prefix:
            return False
        parts = prefix.split('.')
        if len(parts) != 2:
            return False
        cache_label, selector = parts
        if not cache_label or self._is_base32_label(cache_label):
            return False
        if not cache_label.startswith(_CACHE_BUSTER_PREFIX):
            return False
        if selector == 'count':
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
        token_text = selector
        if not self._is_hex_token(token_text):
            self._send_empty_response(
                query_id, qname, qtype, addr,
                reason='flat_invalid',
                include_opt=False,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_invalid',
                'DNS flat stager invalid token',
                lambda: {'dns_id': query_id, 'token': token_text},
            )
            return True
        try:
            token = int(token_text, 16)
        except (TypeError, ValueError):
            token = None
        if token is None:
            self._send_empty_response(
                query_id, qname, qtype, addr,
                reason='flat_invalid',
                include_opt=False,
            )
            log_event(
                self._logger,
                logging.DEBUG,
                'dns.flat_invalid',
                'DNS flat stager token parse failed',
                lambda: {'dns_id': query_id, 'token': token_text},
            )
            return True
        index = (token ^ self._index_seed) & _INDEX_TOKEN_MASK
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
                lambda: {'dns_id': query_id, 'index': index, 'token': token_text},
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
                'token': token_text,
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
            self._base_domain,
            self._label_max_len,
            0,
            use_compression=True,
        )
        if payload_cap is not None and payload_cap < MIN_PACKET_MTU:
            log_event(
                self._logger,
                logging.ERROR,
                'dns.response_payload_cap_invalid',
                'DNS stager response payload cap below minimum',
                lambda: {
                    'context': 'flat_stager',
                    'qname': qname,
                    'qname_wire_len': qname_wire_len,
                    'response_payload_cap': payload_cap,
                    'min_packet_mtu': MIN_PACKET_MTU,
                    'max_packet_size': max_packet_size,
                },
            )
            raise TransportFatalError(
                'DNS stager response payload cap %d below minimum %d (qname=%s)' % (
                    payload_cap,
                    MIN_PACKET_MTU,
                    qname,
                )
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
