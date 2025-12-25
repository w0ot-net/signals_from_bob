# -*- coding: ascii -*-
"""
Logging helpers for the tunnel codebase.
"""

from __future__ import absolute_import

import logging
import sys

from .compat import text_type


DEFAULT_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'


def configure_logging(level='INFO', to_stdout=True, log_file=None):
    """
    Configure tunnel logging.
    """
    logger = logging.getLogger('tunnel')
    logger.setLevel(_coerce_level(level))
    formatter = logging.Formatter(DEFAULT_FORMAT)

    if to_stdout:
        _ensure_handler(logger, logging.StreamHandler, formatter, sys.stdout)

    if log_file:
        _ensure_handler(logger, logging.FileHandler, formatter, log_file)


def get_logger(name):
    """
    Get a module logger under the tunnel namespace.
    """
    if name.startswith('tunnel.'):
        return logging.getLogger(name)
    return logging.getLogger('tunnel.' + name)


def _coerce_level(level):
    if isinstance(level, text_type):
        return getattr(logging, level.upper(), logging.INFO)
    return level


def _ensure_handler(logger, handler_cls, formatter, *args):
    for handler in logger.handlers:
        if isinstance(handler, handler_cls):
            return
    handler = handler_cls(*args)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
