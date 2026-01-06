# -*- coding: ascii -*-
"""
TLS transport configuration validation helpers.
"""

from __future__ import absolute_import

from ...compat import text_type
from ...protocol.constants import PACKET_HEADER_SIZE
from ..transport_base import TransportError
from ..proxy_helpers import validate_proxy_config
from . import tls_handshake_codec as codec
from ...utils import build_host_port_error_map, parse_host_port_or_raise


_SNI_ERROR_MAP = {
    'SNI must be text': 'tls_sni must be text',
    'SNI must be ASCII': 'tls_sni must be ASCII',
    'SNI length invalid': 'tls_sni length invalid',
    'SNI must not start/end with dot': 'tls_sni must not start/end with dot',
    'SNI must not contain empty labels': 'tls_sni must not contain empty labels',
    'SNI contains invalid characters': 'tls_sni contains invalid characters',
    'SNI label length invalid': 'tls_sni label length invalid',
}


_HOST_PORT_ERROR_MAP = build_host_port_error_map(TransportError)


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

    proxy_timeout = None
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

    clienthello_padding_target = _require_non_negative_int(
        config.tls_clienthello_padding_target, 'tls_clienthello_padding_target'
    )

    sni = None
    if config.tls_sni is not None:
        sni = _validate_sni(config.tls_sni)

    alpn_list = None
    if config.tls_alpn is not None:
        alpn_list = _validate_alpn(config.tls_alpn)

    try:
        client_payload_cap = codec.calc_clienthello_payload_cap(
            max_clienthello_bytes, sni=sni, alpn_list=alpn_list,
            padding_target=clienthello_padding_target
        )
        server_payload_cap = codec.calc_serverhello_payload_cap(
            max_serverhello_bytes, alpn_list=alpn_list
        )
    except ValueError as exc:
        raise TransportError('TLS handshake overhead invalid: %s' % exc)
    min_packet = PACKET_HEADER_SIZE + 1
    if client_payload_cap < min_packet:
        raise TransportError(
            'ClientHello max too small for packet MTU '
            '(min_packet=%d cap=%d)' % (min_packet, client_payload_cap)
        )
    if server_payload_cap < min_packet:
        raise TransportError(
            'ServerHello max too small for packet MTU '
            '(min_packet=%d cap=%d)' % (min_packet, server_payload_cap)
        )

    if role == 'client':
        _require_host_port(config.tls_target, 'tls_target')
        proxy_timeout = validate_proxy_config(
            config.tls_http_proxy,
            config.tls_http_proxy_auth,
            config.tls_proxy_timeout,
            connect_timeout,
        )['proxy_timeout']
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
        'clienthello_padding_target': clienthello_padding_target,
        'client_payload_cap': client_payload_cap,
        'server_payload_cap': server_payload_cap,
        'proxy_timeout': proxy_timeout,
    }


def _require_host_port(addr, label):
    if addr is None:
        raise TransportError('%s required' % label)
    parse_host_port_or_raise(addr, _HOST_PORT_ERROR_MAP)


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


def _require_non_negative_int(value, label):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be an integer' % label)
    if value < 0:
        raise TransportError('%s must be >= 0' % label)
    return value


def _validate_sni(value):
    try:
        return codec.normalize_sni(value)
    except ValueError as exc:
        mapped = _SNI_ERROR_MAP.get(str(exc))
        if mapped is None:
            mapped = 'tls_sni invalid: %s' % exc
        raise TransportError(mapped)


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
