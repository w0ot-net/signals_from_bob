# -*- coding: ascii -*-
"""
TLS transport configuration validation helpers.
"""

from __future__ import absolute_import

import re

from ...compat import text_type
from ...protocol.constants import PACKET_HEADER_SIZE
from ..transport_base import TransportError
from . import tls_handshake_codec as codec


_SNI_ALLOWED = re.compile(r'^[A-Za-z0-9.-]+$')


def validate_tls_config(config, role):
    """
    Validate TLS transport config and return normalized values.

    Args:
        config: Config instance
        role: 'client' or 'server'

    Returns:
        dict: normalized values (max sizes, alpn list, etc.)
    """
    if role not in ('client', 'server'):
        raise TransportError('Invalid TLS role: %s' % role)

    pending_timeout = _require_positive_float(
        config.tls_pending_timeout, 'tls_pending_timeout'
    )
    connect_timeout = _require_positive_float(
        config.tls_connect_timeout, 'tls_connect_timeout'
    )
    handshake_timeout = _require_positive_float(
        config.tls_handshake_timeout, 'tls_handshake_timeout'
    )
    if pending_timeout < connect_timeout or pending_timeout < handshake_timeout:
        raise TransportError('tls_pending_timeout must be >= connect/handshake')

    max_clienthello_bytes = _require_positive_int(
        config.tls_max_clienthello_bytes, 'tls_max_clienthello_bytes'
    )
    max_serverhello_bytes = _require_positive_int(
        config.tls_max_serverhello_bytes, 'tls_max_serverhello_bytes'
    )
    max_clienthello_bytes = min(max_clienthello_bytes, codec.TLS_MAX_RECORD_SIZE)
    max_serverhello_bytes = min(max_serverhello_bytes, codec.TLS_MAX_RECORD_SIZE)

    sni = None
    if config.tls_sni is not None:
        sni = _validate_sni(config.tls_sni)

    alpn_list = None
    if config.tls_alpn is not None:
        alpn_list = _validate_alpn(config.tls_alpn)

    try:
        client_payload_cap = codec.calc_clienthello_payload_cap(
            max_clienthello_bytes, sni=sni, alpn_list=alpn_list
        )
        server_payload_cap = codec.calc_serverhello_payload_cap(
            max_serverhello_bytes, alpn_list=alpn_list
        )
    except ValueError as exc:
        raise TransportError('TLS handshake overhead invalid: %s' % exc)
    min_packet = PACKET_HEADER_SIZE + 1
    if client_payload_cap < min_packet:
        raise TransportError('ClientHello max too small for packet MTU')
    if server_payload_cap < min_packet:
        raise TransportError('ServerHello max too small for packet MTU')

    if role == 'client':
        _require_host_port(config.tls_target, 'tls_target')
    if role == 'server':
        _require_host_port(config.tls_listen_addr, 'tls_listen_addr')

    return {
        'pending_timeout': pending_timeout,
        'connect_timeout': connect_timeout,
        'handshake_timeout': handshake_timeout,
        'max_clienthello_bytes': max_clienthello_bytes,
        'max_serverhello_bytes': max_serverhello_bytes,
        'sni': sni,
        'alpn_list': alpn_list,
        'client_payload_cap': client_payload_cap,
        'server_payload_cap': server_payload_cap,
    }


def parse_host_port(addr):
    if not isinstance(addr, text_type):
        raise TransportError('Address must be text')
    if ':' not in addr:
        raise TransportError('Address must include port')
    host, port_text = addr.rsplit(':', 1)
    if not host:
        raise TransportError('Address host required')
    try:
        port = int(port_text, 10)
    except ValueError:
        raise TransportError('Address port invalid')
    if port < 1 or port > 65535:
        raise TransportError('Address port out of range')
    return host, port


def _require_host_port(addr, label):
    if addr is None:
        raise TransportError('%s required' % label)
    parse_host_port(addr)


def _require_positive_float(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be a number' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value


def _require_positive_int(value, label):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be an integer' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value


def _validate_sni(value):
    if not isinstance(value, text_type):
        raise TransportError('tls_sni must be text')
    try:
        value.encode('ascii')
    except UnicodeError:
        raise TransportError('tls_sni must be ASCII')
    if not value or len(value) > 253:
        raise TransportError('tls_sni length invalid')
    if value.startswith('.') or value.endswith('.'):
        raise TransportError('tls_sni must not start/end with dot')
    if '..' in value:
        raise TransportError('tls_sni must not contain empty labels')
    if not _SNI_ALLOWED.match(value):
        raise TransportError('tls_sni contains invalid characters')
    labels = value.split('.')
    for label in labels:
        if not label or len(label) > 63:
            raise TransportError('tls_sni label length invalid')
    return value


def _validate_alpn(value):
    if not isinstance(value, text_type):
        raise TransportError('tls_alpn must be text')
    tokens = [token.strip() for token in value.split(',')]
    if not tokens or any(not token for token in tokens):
        raise TransportError('tls_alpn contains empty entries')
    alpn_list = []
    for token in tokens:
        try:
            token.encode('ascii')
        except UnicodeError:
            raise TransportError('tls_alpn must be ASCII')
        if len(token) > 255:
            raise TransportError('tls_alpn entry too long')
        alpn_list.append(token)
    return alpn_list
