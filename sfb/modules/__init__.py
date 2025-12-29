# -*- coding: ascii -*-
"""
Application modules for the tunnel.
"""

from __future__ import absolute_import

from .base_module import BaseModule, RequestResponseMixin, ModuleError, blocking
from .file_transfer.file_transfer import FileTransferModule
from .module_loader import ModuleLoader, ModuleLoadError

AVAILABLE_MODULES = {
    'file_transfer': FileTransferModule,
}
