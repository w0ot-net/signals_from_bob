# -*- coding: ascii -*-
"""
Application modules for the tunnel.
"""

from __future__ import absolute_import

from .base_module import BaseModule, RequestResponseMixin, ModuleError, blocking
from .file_transfer.file_transfer import FileTransferModule
from .socks.socks_server import SocksServerModule
from .socks.socks_relay import SocksRelayModule

AVAILABLE_MODULES = {
    'file_transfer': FileTransferModule,
    'socks_server': SocksServerModule,
    'socks_relay': SocksRelayModule,
}
