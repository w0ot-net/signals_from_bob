# -*- coding: ascii -*-
"""NC Linux control message helpers."""

from __future__ import absolute_import

from ...control_message import ControlMessage

T_NC = 'nc'


def nc_bind(rid, ch, fd):
    """Request to bind a channel to a local file descriptor."""
    return ControlMessage(T_NC, 'bind', rid=rid, ch=ch, fd=fd)


def nc_bind_ok(rid, ch):
    """Bind request succeeded."""
    return ControlMessage(T_NC, 'bind_ok', rid=rid, ch=ch)


def nc_err(rid, ch, code, reason):
    """Bind request failed."""
    return ControlMessage(T_NC, 'err', rid=rid, ch=ch, code=code, reason=reason)
