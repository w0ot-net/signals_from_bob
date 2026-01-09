# -*- coding: ascii -*-
"""
Application modules for the tunnel.
"""

from __future__ import absolute_import

import importlib

from .base_module import BaseModule, RequestResponseMixin, ModuleError, blocking

AVAILABLE_MODULES = {
    'file_transfer': ('sfb.modules.file_transfer.file_transfer', 'FileTransferModule'),
    'nc_linux': ('sfb.modules.nc_linux.nc_linux', 'NcLinuxModule'),
    'port_fwd_server': ('sfb.modules.port_fwd.port_fwd_server', 'PortForwardServerModule'),
    'port_fwd_relay': ('sfb.modules.port_fwd.port_fwd_relay', 'PortForwardRelayModule'),
    'socks': ('sfb.modules.socks.socks_server', 'SocksServerModule'),
    'socks_relay': ('sfb.modules.socks.socks_relay', 'SocksRelayModule'),
}

CLI_MODULES = {
    'file_transfer': ('sfb.modules.file_transfer.file_transfer', 'FileTransferModule'),
    'nc_linux': ('sfb.modules.nc_linux.nc_linux', 'NcLinuxModule'),
    'port_fwd_server': ('sfb.modules.port_fwd.port_fwd_server', 'PortForwardServerModule'),
    'port_fwd_relay': ('sfb.modules.port_fwd.port_fwd_relay', 'PortForwardRelayModule'),
    'socks': ('sfb.modules.socks.socks_server', 'SocksServerModule'),
}


def _load_symbol(module_name, symbol_name):
    module = importlib.import_module(module_name)
    try:
        return getattr(module, symbol_name)
    except AttributeError:
        raise ImportError('Unable to load %s from %s' % (symbol_name, module_name))


def get_available_module_class(name):
    spec = AVAILABLE_MODULES.get(name)
    if spec is None:
        return None
    return _load_symbol(spec[0], spec[1])


def get_cli_module_class(name):
    spec = CLI_MODULES.get(name)
    if spec is None:
        return None
    return _load_symbol(spec[0], spec[1])


def list_available_modules():
    return sorted(AVAILABLE_MODULES.keys())


def list_cli_modules():
    return sorted(CLI_MODULES.keys())
