# -*- coding: ascii -*-
"""
TLS handshake bump transport configuration validation.
"""

from __future__ import absolute_import

import re

from ...compat import text_type
from ...protocol.constants import PACKET_HEADER_SIZE
from ..transport_base import TransportError
from . import tls_handshake_bump_cert_template as cert_template
from . import tls_handshake_bump_codec as codec


_DOMAIN_ALLOWED = re.compile(r'^[A-Za-z0-9.-]+$')


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

    pending_timeout = _require_positive_float(
        config.tls_bump_pending_timeout, 'tls_bump_pending_timeout'
    )
    connect_timeout = _require_positive_float(
        config.tls_bump_connect_timeout, 'tls_bump_connect_timeout'
    )
    handshake_timeout = _require_positive_float(
        config.tls_bump_handshake_timeout, 'tls_bump_handshake_timeout'
    )
    if pending_timeout < connect_timeout or pending_timeout < handshake_timeout:
        raise TransportError('tls_bump_pending_timeout must be >= connect/handshake')

    base_domain = _validate_base_domain(config.tls_bump_base_domain)
    cn_max_len = cert_template.CN_LEN
    cn_override = getattr(config, 'tls_bump_cn_max_len', None)
    if role == 'client' and cn_override is not None:
        cn_max_len = _require_positive_int(cn_override, 'tls_bump_cn_max_len')

    sni_payload_cap = codec.calc_sni_payload_cap(base_domain)
    cn_payload_cap = codec.calc_cn_payload_cap(cn_max_len)
    min_packet = PACKET_HEADER_SIZE + 1
    if sni_payload_cap < min_packet:
        raise TransportError('SNI max too small for packet MTU')
    if cn_payload_cap < min_packet:
        raise TransportError('CN max too small for packet MTU')

    max_clienthello_bytes = _require_positive_int(
        config.tls_bump_max_clienthello_bytes, 'tls_bump_max_clienthello_bytes'
    )
    max_clienthello_bytes = min(max_clienthello_bytes, codec.TLS_MAX_RECORD_SIZE)

    proxy_timeout = None
    proxy_addr = None
    proxy_auth = None
    request_path = None
    response_mode = None
    response_regex = None
    response_regex_text = None
    if role == 'client':
        _require_host_port(config.tls_bump_target, 'tls_bump_target')
        proxy_addr, proxy_auth, proxy_timeout = _validate_proxy_config(
            config.tls_bump_http_proxy,
            config.tls_bump_http_proxy_auth,
            config.tls_bump_proxy_timeout,
            connect_timeout,
        )
        request_path = _validate_request_path(config.tls_bump_request_path)
        response_mode = _normalize_response_mode(
            config.tls_bump_response_mode,
            config.tls_bump_response_regex,
        )
        if response_mode == 'regex':
            response_regex_text = _require_ascii_text(
                config.tls_bump_response_regex, 'tls_bump_response_regex'
            )
            response_regex = _compile_response_regex(response_regex_text)
    else:
        _require_host_port(config.tls_bump_listen_addr, 'tls_bump_listen_addr')

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
        'response_mode': response_mode,
        'response_regex': response_regex,
        'response_regex_text': response_regex_text,
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
    if value.startswith('.') or value.endswith('.'):
        raise TransportError('tls_bump_base_domain must not start/end with dot')
    if '..' in value:
        raise TransportError('tls_bump_base_domain must not contain empty labels')
    if not _DOMAIN_ALLOWED.match(value):
        raise TransportError('tls_bump_base_domain contains invalid characters')
    labels = value.split('.')
    for label in labels:
        if not label or len(label) > codec.MAX_LABEL_LEN:
            raise TransportError('tls_bump_base_domain label length invalid')
    if len(value) > codec.MAX_NAME_LEN:
        raise TransportError('tls_bump_base_domain length invalid')
    return value.lower()


def _validate_request_path(value):
    value = _require_ascii_text(value, 'tls_bump_request_path')
    if not value.startswith('/'):
        raise TransportError('tls_bump_request_path must start with /')
    if any(ch.isspace() for ch in value):
        raise TransportError('tls_bump_request_path must not contain whitespace')
    return value


def _compile_response_regex(value):
    try:
        compiled = re.compile(value.encode('ascii'), re.DOTALL | re.IGNORECASE)
    except re.error as exc:
        raise TransportError('tls_bump_response_regex invalid: %s' % exc)
    if compiled.groups < 1:
        raise TransportError('tls_bump_response_regex must include a capture group')
    return compiled


def _normalize_response_mode(value, response_regex):
    if value is None:
        if response_regex:
            return 'regex'
        return 'scan'
    if not isinstance(value, text_type):
        raise TransportError('tls_bump_response_mode must be text')
    try:
        value.encode('ascii')
    except UnicodeError:
        raise TransportError('tls_bump_response_mode must be ASCII')
    value = value.strip().lower()
    if not value:
        if response_regex:
            return 'regex'
        return 'scan'
    if value not in ('regex', 'scan'):
        raise TransportError('tls_bump_response_mode must be regex or scan')
    return value


def _validate_proxy_config(proxy, proxy_auth, proxy_timeout, connect_timeout):
    proxy_timeout_value = None
    proxy_addr = None
    proxy_auth_value = None
    if proxy is not None:
        proxy_addr = _validate_proxy_addr(proxy)
        if proxy_auth is not None:
            proxy_auth_value = _validate_proxy_auth(proxy_auth)
        if proxy_timeout is None:
            proxy_timeout_value = connect_timeout
        else:
            proxy_timeout_value = _require_positive_float(
                proxy_timeout, 'tls_bump_proxy_timeout'
            )
    else:
        if proxy_auth is not None:
            raise TransportError('tls_bump_http_proxy_auth requires tls_bump_http_proxy')
        if proxy_timeout is not None:
            proxy_timeout_value = _require_positive_float(
                proxy_timeout, 'tls_bump_proxy_timeout'
            )
    return proxy_addr, proxy_auth_value, proxy_timeout_value


def _validate_proxy_addr(value):
    value = _require_ascii_text(value, 'tls_bump_http_proxy')
    if any(ch.isspace() for ch in value):
        raise TransportError('tls_bump_http_proxy must not contain whitespace')
    parse_host_port(value)
    return value


def _validate_proxy_auth(value):
    value = _require_ascii_text(value, 'tls_bump_http_proxy_auth')
    if ':' not in value:
        raise TransportError('tls_bump_http_proxy_auth must be user:pass')
    return value
