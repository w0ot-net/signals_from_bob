# -*- coding: ascii -*-
"""
Transport layer for tunnel protocol.

All transports use a request/response pattern. Alice sends requests via
exchange(), Bob receives via recv() and responds via the responder callback.
"""

from __future__ import absolute_import

from .transport_base import (
    RequestResponseTransport,
    RequestResponseServer,
    TransportError,
)

__all__ = [
    'RequestResponseTransport',
    'RequestResponseServer',
    'TransportError',
]
