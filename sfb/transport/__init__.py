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
]
