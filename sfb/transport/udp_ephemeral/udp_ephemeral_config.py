# -*- coding: ascii -*-
"""
UDP ephemeral transport configuration validation helpers.
"""

from __future__ import absolute_import

from ...compat import text_type
from ..transport_base import TransportError


def validate_udp_ephemeral_config(config, role):
    """
    Validate UDP ephemeral transport config and return normalized values.

    Args:
        config: Config instance
        role: 'client' or 'server'

    Returns:
        dict: normalized values (payload_mtu, timeouts, addrs)
    """
    if role not in ('client', 'server'):
        raise TransportError('Invalid UDP ephemeral role: %s' % role)

    payload_mtu = _require_positive_int(
        config.udp_ephemeral_payload_mtu, 'udp_ephemeral_payload_mtu'
    )
    pending_timeout = _require_positive_float(
        config.udp_ephemeral_pending_timeout, 'udp_ephemeral_pending_timeout'
    )
    reuse_minutes = _require_non_negative_float(
        config.udp_ephemeral_source_port_reuse_minutes,
        'udp_ephemeral_source_port_reuse_minutes'
    )

    target_addr = None
    listen_addr = None
    if role == 'client':
        if config.udp_ephemeral_target is None:
            raise TransportError('udp_ephemeral_target required')
        target_addr = parse_host_port(config.udp_ephemeral_target)
    else:
        if config.udp_ephemeral_listen_addr is None:
            raise TransportError('udp_ephemeral_listen_addr required')
        listen_addr = parse_host_port(config.udp_ephemeral_listen_addr)

    return {
        'payload_mtu': payload_mtu,
        'pending_timeout': pending_timeout,
        'reuse_minutes': reuse_minutes,
        'target_addr': target_addr,
        'listen_addr': listen_addr,
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


def _require_positive_float(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be a number' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value


def _require_non_negative_float(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be a number' % label)
    if value < 0:
        raise TransportError('%s must be >= 0' % label)
    return value


def _require_positive_int(value, label):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise TransportError('%s must be an integer' % label)
    if value <= 0:
        raise TransportError('%s must be > 0' % label)
    return value
