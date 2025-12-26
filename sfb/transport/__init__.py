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

__all__ = [
    'Transport',
    'Server',
    'TransportError',
]
