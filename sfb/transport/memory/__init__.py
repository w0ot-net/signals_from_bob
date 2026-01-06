# -*- coding: ascii -*-
"""
In-memory transport for local testing.

Provides a Transport/Server pair backed by in-process queues. Useful for
unit tests and simulations without any network I/O.
"""

from __future__ import absolute_import

from .memory_client import InMemoryTransport
from .memory_server import InMemoryServer
from .memory_link import _InMemoryLink


def create_inmemory_transport_pair(config, send_packet_mtu=None,
                                   recv_packet_mtu=None):
    """
    Create a connected in-memory Transport/Server pair.

    Args:
        config: Config instance
        send_packet_mtu: Optional request MTU (Alice->Bob)
        recv_packet_mtu: Optional response MTU (Bob->Alice)
    Returns:
        tuple: (InMemoryTransport, InMemoryServer)
    """
    link = _InMemoryLink(
        send_packet_mtu, recv_packet_mtu, config,
    )
    return (
        InMemoryTransport(
            config, link=link, send_packet_mtu=send_packet_mtu,
            recv_packet_mtu=recv_packet_mtu,
        ),
        InMemoryServer(
            config, link=link, send_packet_mtu=recv_packet_mtu,
            recv_packet_mtu=send_packet_mtu,
        ),
    )


__all__ = [
    'InMemoryTransport',
    'InMemoryServer',
    'create_inmemory_transport_pair',
]
