# -*- coding: ascii -*-
"""
Transport layer for tunnel protocol.

All transports use a pipelined request/response pattern. Alice sends via
send() and receives via recv(). Bob receives via recv() and responds via
the responder callback.
"""

from __future__ import absolute_import

from .transport_base import (
    Transport,
    Server,
    TransportError,
)

from .lossy import (
    NetworkImpairment,
    LossyTransport,
    LossyServer,
    # Presets
    no_impairment,
    high_latency,
    moderate_loss,
    heavy_loss,
    burst_loss,
    extreme_conditions,
    chaos,
)

from .dns import DnsClient, DnsServer
from .icmp import IcmpClient, IcmpServer


def get_transport_class(name, role):
    """
    Get transport class for the given name and role.

    Args:
        name: Transport name ('dns', etc.)
        role: 'client' or 'server'

    Returns:
        Transport class

    Raises:
        ValueError: If transport or role not found
    """
    if name not in TRANSPORTS:
        raise ValueError('Unknown transport: %s (available: %s)' %
                         (name, ', '.join(TRANSPORTS.keys())))
    transport = TRANSPORTS[name]
    if role not in transport:
        raise ValueError('Transport %s does not support role: %s' % (name, role))
    return transport[role]


# Transport registry: name -> {role -> class}
TRANSPORTS = {
    'dns': {
        'client': DnsClient,
        'server': DnsServer,
    },
    'icmp': {
        'client': IcmpClient,
        'server': IcmpServer,
    },
}


__all__ = [
    'Transport',
    'Server',
    'TransportError',
    'NetworkImpairment',
    'LossyTransport',
    'LossyServer',
    'no_impairment',
    'high_latency',
    'moderate_loss',
    'heavy_loss',
    'burst_loss',
    'extreme_conditions',
    'chaos',
    'IcmpClient',
    'IcmpServer',
    'TRANSPORTS',
    'get_transport_class',
]
