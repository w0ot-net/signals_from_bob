# -*- coding: ascii -*-
"""
Selector helpers for TLS handshake bump transport.
"""

from __future__ import absolute_import

import select

from ..transport_base import TransportError


_POLL_AVAILABLE = hasattr(select, 'poll')
_SELECT_FD_LIMIT = getattr(select, 'FD_SETSIZE', None)

if _POLL_AVAILABLE:
    _POLLIN = select.POLLIN
    _POLLOUT = select.POLLOUT
    _POLLPRI = getattr(select, 'POLLPRI', 0)
    _POLLERR = getattr(select, 'POLLERR', 0)
    _POLLHUP = getattr(select, 'POLLHUP', 0)
    _POLLNVAL = getattr(select, 'POLLNVAL', 0)


def _select_wait(read_list, write_list, timeout):
    if _SELECT_FD_LIMIT is not None:
        total = len(read_list) + len(write_list)
        if total > _SELECT_FD_LIMIT:
            raise TransportError(
                'select() fd limit exceeded: %d > %d' % (total, _SELECT_FD_LIMIT)
            )
    try:
        return select.select(read_list, write_list, [], timeout)
    except select.error as e:
        raise TransportError('Select failed: %s' % e)


def _poll_wait(read_list, write_list, timeout):
    poller = select.poll()
    fd_map = {}
    events = {}
    for sock in read_list:
        fd = sock.fileno()
        fd_map[fd] = sock
        events[fd] = events.get(fd, 0) | _POLLIN | _POLLPRI
    for sock in write_list:
        fd = sock.fileno()
        fd_map[fd] = sock
        events[fd] = events.get(fd, 0) | _POLLOUT
    for fd, mask in events.items():
        poller.register(fd, mask)
    if timeout is None:
        ms = None
    else:
        ms = max(0, int(timeout * 1000))
    try:
        ready = poller.poll(ms)
    except select.error as e:
        raise TransportError('Select failed: %s' % e)
    ready_r = []
    ready_w = []
    error_mask = _POLLERR | _POLLHUP | _POLLNVAL
    for fd, event in ready:
        sock = fd_map.get(fd)
        if sock is None:
            continue
        if event & (_POLLIN | _POLLPRI | error_mask):
            ready_r.append(sock)
        if event & (_POLLOUT | error_mask):
            ready_w.append(sock)
    return ready_r, ready_w


class SocketSelector(object):
    def __init__(self):
        self._use_poll = _POLL_AVAILABLE

    def wait(self, read_list, write_list, timeout):
        if self._use_poll:
            return _poll_wait(read_list, write_list, timeout)
        return _select_wait(read_list, write_list, timeout)
