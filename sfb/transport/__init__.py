# -*- coding: ascii -*-
"""
Transport layer for tunnel protocol.

All transports use a pipelined request/response pattern. Alice sends via
send() and receives via recv(). Bob receives via recv() and responds via
the responder callback.
"""

from __future__ import absolute_import

import importlib

from .transport_base import (
    Transport,
    Server,
    TransportError,
)

_LOSSY_CACHE = None

def _load_symbol(module_name, symbol_name):
    module = importlib.import_module(module_name)
    try:
        return getattr(module, symbol_name)
    except AttributeError:
        raise ImportError('Unable to load %s from %s' % (symbol_name, module_name))


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
    module_name, symbol_name = transport[role]
    try:
        return _load_symbol(module_name, symbol_name)
    except ImportError as exc:
        raise TransportError('Transport %s unavailable: %s' % (name, exc))


def get_transport_names():
    return sorted(TRANSPORTS.keys())


def create_inmemory_transport_pair(config, send_packet_mtu=None,
                                   recv_packet_mtu=None):
    module = importlib.import_module('sfb.transport.memory')
    return module.create_inmemory_transport_pair(
        config,
        send_packet_mtu=send_packet_mtu,
        recv_packet_mtu=recv_packet_mtu,
    )


def load_lossy():
    global _LOSSY_CACHE
    if _LOSSY_CACHE is None:
        module = importlib.import_module('sfb.transport.lossy')
        _LOSSY_CACHE = (
            module.NetworkImpairment,
            module.LossyTransport,
            module.LossyServer,
        )
    return _LOSSY_CACHE


# Transport registry: name -> {role -> (module, class)}
TRANSPORTS = {
    'dns': {
        'client': ('sfb.transport.dns', 'DnsClient'),
        'server': ('sfb.transport.dns', 'DnsServer'),
    },
    'icmp': {
        'client': ('sfb.transport.icmp', 'IcmpClient'),
        'server': ('sfb.transport.icmp', 'IcmpServer'),
    },
    'udp_ephemeral': {
        'client': ('sfb.transport.udp_ephemeral', 'UdpEphemeralClient'),
        'server': ('sfb.transport.udp_ephemeral', 'UdpEphemeralServer'),
    },
    'tls_handshake': {
        'client': ('sfb.transport.tls_handshake', 'TlsClient'),
        'server': ('sfb.transport.tls_handshake', 'TlsServer'),
    },
    'tls_handshake_bump': {
        'client': ('sfb.transport.tls_handshake_bump', 'TlsHandshakeBumpClient'),
        'server': ('sfb.transport.tls_handshake_bump', 'TlsHandshakeBumpServer'),
    },
    'memory': {
        'client': ('sfb.transport.memory', 'InMemoryTransport'),
        'server': ('sfb.transport.memory', 'InMemoryServer'),
    },
}


__all__ = [
    'Transport',
    'Server',
    'TransportError',
    'create_inmemory_transport_pair',
    'load_lossy',
    'TRANSPORTS',
    'get_transport_class',
    'get_transport_names',
]
