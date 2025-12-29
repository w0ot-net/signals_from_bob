# -*- coding: ascii -*-
"""
SOCKS5 proxy module.

Provides SOCKS5 proxy functionality over the tunnel:
- SocksServerModule: Runs on Bob, accepts SOCKS5 clients
- SocksRelayModule: Runs on Alice, makes outbound connections
"""

from __future__ import absolute_import

from .socks_server import SocksServerModule
from .socks_relay import SocksRelayModule

__all__ = [
    'SocksServerModule',
    'SocksRelayModule',
]
