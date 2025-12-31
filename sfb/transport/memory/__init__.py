# -*- coding: ascii -*-
"""
In-memory transport for local testing.

Provides a Transport/Server pair backed by in-process queues. Useful for
unit tests and simulations without any network I/O.
"""

from __future__ import absolute_import

from .client import InMemoryTransport
from .server import InMemoryServer
from .link import _InMemoryLink


def create_inmemory_transport_pair(config, send_mtu=None, recv_mtu=None,
                                   max_pending=None):
    """
    Create a connected in-memory Transport/Server pair.

    Args:
        config: Config instance
        send_mtu: Optional request MTU (Alice->Bob)
        recv_mtu: Optional response MTU (Bob->Alice)
        max_pending: Optional max in-flight requests

    Returns:
        tuple: (InMemoryTransport, InMemoryServer)
    """
    link = _InMemoryLink(
        send_mtu, recv_mtu, max_pending, config,
    )
    return (
        InMemoryTransport(
            config, link=link, send_mtu=send_mtu, recv_mtu=recv_mtu,
            max_pending=max_pending,
        ),
        InMemoryServer(
            config, link=link, send_mtu=recv_mtu, recv_mtu=send_mtu,
            max_pending=max_pending,
        ),
    )


__all__ = [
    'InMemoryTransport',
    'InMemoryServer',
    'create_inmemory_transport_pair',
]
