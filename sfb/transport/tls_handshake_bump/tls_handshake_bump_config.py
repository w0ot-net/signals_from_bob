# -*- coding: ascii -*-
"""
TLS handshake bump transport configuration validation.
"""

from __future__ import absolute_import

from ...compat import text_type
from ...protocol.constants import MIN_PACKET_MTU
from ..transport_base import TransportError
from ..proxy_helpers import validate_proxy_config
from . import tls_handshake_bump_cert_template as cert_template
from . import tls_handshake_bump_codec as codec
from ...utils import build_host_port_error_map, parse_host_port_or_raise


_BASE_DOMAIN_ERROR_MAP = {
    'Base domain must be text': 'tls_bump_base_domain must be text',
    'Base domain must be ASCII': 'tls_bump_base_domain must be ASCII',
    'Base domain required': 'tls_bump_base_domain required',
    'Base domain must not start/end with dot': 'tls_bump_base_domain must not start/end with dot',
    'Base domain must not contain empty labels': 'tls_bump_base_domain must not contain empty labels',
    'Base domain contains invalid characters': 'tls_bump_base_domain contains invalid characters',
    'Empty label in name': 'tls_bump_base_domain must not contain empty labels',
    'Label exceeds max length': 'tls_bump_base_domain label length invalid',
    'Name exceeds max length': 'tls_bump_base_domain length invalid',
}


_HOST_PORT_ERROR_MAP = build_host_port_error_map(TransportError)


def validate_tls_bump_config(config, role):
    """
    Validate TLS handshake bump transport config and return normalized values.

    Args:
        config: Config instance
        role: 'client' or 'server'

    Returns:
        dict: normalized values (timeouts, MTUs, regex, etc.)
    """
    if role not in ('client', 'server'):
        raise TransportError('Invalid TLS bump role: %s' % role)

    connect_timeout = _require_positive_float(
        config.tls_bump_connect_timeout, 'tls_bump_connect_timeout'
    )
    handshake_timeout = _require_positive_float(
        config.tls_bump_handshake_timeout, 'tls_bump_handshake_timeout'
    )

    base_domain = _validate_base_domain(config.tls_bump_base_domain)
    cn_max_len = cert_template.CN_LEN
    cn_override = getattr(config, 'tls_bump_cn_max_len', None)
    if role == 'client' and cn_override is not None:
        cn_max_len = _require_positive_int(cn_override, 'tls_bump_cn_max_len')

    sni_payload_cap = codec.calc_sni_payload_cap(base_domain)
    cn_payload_cap = codec.calc_cn_payload_cap(cn_max_len)
    min_packet = MIN_PACKET_MTU
    if sni_payload_cap < min_packet:
        raise TransportError(
            'SNI max too small for packet MTU '
            '(min_packet=%d cap=%d)' % (min_packet, sni_payload_cap)
        )
    if cn_payload_cap < min_packet:
        raise TransportError(
            'CN max too small for packet MTU '
            '(min_packet=%d cap=%d)' % (min_packet, cn_payload_cap)
        )

    max_clienthello_bytes = _require_positive_int(
        config.tls_bump_max_clienthello_bytes, 'tls_bump_max_clienthello_bytes'
    )
    max_clienthello_bytes = min(max_clienthello_bytes, codec.TLS_MAX_RECORD_SIZE)

    proxy_timeout = None
    proxy_addr = None
    proxy_auth = None
    request_path = None
    if role == 'client':
        _require_host_port(config.tls_bump_target, 'tls_bump_target')
        proxy_values = validate_proxy_config(
            config.tls_bump_http_proxy,
            config.tls_bump_http_proxy_auth,
            config.tls_bump_proxy_timeout,
            connect_timeout,
            proxy_label='tls_bump_http_proxy',
            proxy_auth_label='tls_bump_http_proxy_auth',
            proxy_timeout_label='tls_bump_proxy_timeout',
        )
        proxy_addr = proxy_values['proxy_addr']
        proxy_auth = proxy_values['proxy_auth']
        proxy_timeout = proxy_values['proxy_timeout']
        request_path = _validate_request_path(config.tls_bump_request_path)
        pending_timeout = connect_timeout + handshake_timeout
        if proxy_addr is not None and proxy_timeout is not None:
            pending_timeout += proxy_timeout
    else:
        _require_host_port(config.tls_bump_listen_addr, 'tls_bump_listen_addr')
        pending_timeout = handshake_timeout

    return {
        'pending_timeout': pending_timeout,
        'connect_timeout': connect_timeout,
        'handshake_timeout': handshake_timeout,
        'base_domain': base_domain,
        'sni_payload_cap': sni_payload_cap,
        'cn_payload_cap': cn_payload_cap,
        'cn_max_len': cn_max_len,
        'max_clienthello_bytes': max_clienthello_bytes,
        'proxy_addr': proxy_addr,
        'proxy_auth': proxy_auth,
        'proxy_timeout': proxy_timeout,
        'request_path': request_path,
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


def _require_ascii_text(value, label):
    if not isinstance(value, text_type):
        raise TransportError('%s must be text' % label)
    if not value:
        raise TransportError('%s must not be empty' % label)
    try:
        value.encode('ascii')
    except UnicodeError:
        raise TransportError('%s must be ASCII' % label)
    return value


def _validate_base_domain(value):
    value = _require_ascii_text(value, 'tls_bump_base_domain')
    value = value.rstrip('.')
    if not value:
        raise TransportError('tls_bump_base_domain required')
    try:
        return codec.normalize_domain(value)
    except ValueError as exc:
        mapped = _BASE_DOMAIN_ERROR_MAP.get(str(exc))
        if mapped is None:
            mapped = 'tls_bump_base_domain invalid: %s' % exc
        raise TransportError(mapped)


def _validate_request_path(value):
    value = _require_ascii_text(value, 'tls_bump_request_path')
    if not value.startswith('/'):
        raise TransportError('tls_bump_request_path must start with /')
    if any(ch.isspace() for ch in value):
        raise TransportError('tls_bump_request_path must not contain whitespace')
    return value
