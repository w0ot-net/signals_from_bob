# -*- coding: ascii -*-
"""
Shared socket error constants.
"""

from __future__ import absolute_import

import errno


def _build_errno_set(base, names):
    values = set(base)
    for name in names:
        value = getattr(errno, name, None)
        if value is not None:
            values.add(value)
    return values


IN_PROGRESS_ERRNOS = _build_errno_set(
    [errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY],
    ('WSAEINPROGRESS', 'WSAEWOULDBLOCK', 'WSAEALREADY'),
)
TEMP_ERRORS = _build_errno_set(
    [errno.EWOULDBLOCK, errno.EAGAIN],
    ('WSAEWOULDBLOCK', 'WSAEINTR'),
)
SOFT_CONNECT_ERRORS = _build_errno_set(
    [errno.ECONNREFUSED],
    ('WSAECONNREFUSED',),
)
RESET_ERRORS = _build_errno_set(
    [errno.ECONNRESET],
    ('WSAECONNRESET',),
)

PHASE_CONNECT = 'connect'
PHASE_PROXY = 'proxy'
PHASE_HANDSHAKE = 'handshake'
PHASE_REQUEST = 'request'
PHASE_RESPONSE = 'response'
