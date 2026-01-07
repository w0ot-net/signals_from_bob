# -*- coding: ascii -*-
"""
UDP ephemeral transport configuration validation helpers.
"""

from __future__ import absolute_import

from ..transport_base import TransportError
from ...utils import build_host_port_error_map, parse_host_port_or_raise


_HOST_PORT_ERROR_MAP = build_host_port_error_map(TransportError)


def validate_udp_ephemeral_config(config, role):
    """
    Validate UDP ephemeral transport config and return normalized values.

    Args:
        config: Config instance
        role: 'client' or 'server'

    Returns:
        dict: normalized values (packet_mtu, timeouts, addrs)
    """
    if role not in ('client', 'server'):
        raise TransportError('Invalid UDP ephemeral role: %s' % role)

    packet_mtu = _require_positive_int(
        config.udp_ephemeral_packet_mtu, 'udp_ephemeral_packet_mtu'
    )
    pending_timeout = _require_positive_float(
        config.udp_ephemeral_pending_timeout, 'udp_ephemeral_pending_timeout'
    )
    reuse_seconds = _require_non_negative_float(
        config.udp_ephemeral_source_port_reuse_seconds,
        'udp_ephemeral_source_port_reuse_seconds'
    )

    target_addr = None
    listen_addr = None
    if role == 'client':
        if config.udp_ephemeral_target is None:
            raise TransportError('udp_ephemeral_target required')
        target_addr = parse_host_port_or_raise(
            config.udp_ephemeral_target,
            _HOST_PORT_ERROR_MAP,
        )
    else:
        if config.udp_ephemeral_listen_addr is None:
            raise TransportError('udp_ephemeral_listen_addr required')
        listen_addr = parse_host_port_or_raise(
            config.udp_ephemeral_listen_addr,
            _HOST_PORT_ERROR_MAP,
        )

    return {
        'packet_mtu': packet_mtu,
        'pending_timeout': pending_timeout,
        'reuse_seconds': reuse_seconds,
        'target_addr': target_addr,
        'listen_addr': listen_addr,
    }


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
