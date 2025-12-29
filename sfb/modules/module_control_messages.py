# -*- coding: ascii -*-
"""
Module loading control message helpers.
"""

from __future__ import absolute_import

from ..control_message import ControlMessage

T_MOD = 'mod'


def mod_load(name):
    """Request to load a module by name."""
    return ControlMessage(T_MOD, 'load', name=name)


def mod_load_ok(name):
    """Success response after loading a module."""
    return ControlMessage(T_MOD, 'load_ok', name=name)


def mod_load_err(name, reason):
    """Error response when module loading fails."""
    return ControlMessage(T_MOD, 'load_err', name=name, reason=reason)
