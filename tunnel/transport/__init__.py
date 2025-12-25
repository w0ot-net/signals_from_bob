# -*- coding: ascii -*-
"""
Transport layer for tunnel protocol.

Transports handle the underlying I/O mechanism (DNS, HTTP, etc.) and
provide a uniform interface for the reliability and muxer layers.
"""

from __future__ import absolute_import

from .transport_base import (
    RequestResponseTransport,
    RequestResponseServer,
    StreamTransport,
    DatagramTransport,
    TransportError,
)

__all__ = [
    'RequestResponseTransport',
    'RequestResponseServer',
    'StreamTransport',
    'DatagramTransport',
    'TransportError',
]
