# -*- coding: ascii -*-
"""Shared link for in-memory transport."""

from __future__ import absolute_import

import threading

from ...compat import queue

from ...protocol.constants import DEFAULT_MAX_PACKET_SIZE
from ...config import Config


class _InMemoryLink(object):
    """Shared state between in-memory client and server."""

    __slots__ = (
        'request_mtu', 'response_mtu', 'max_in_flight',
        '_requests', '_responses', '_closed', '_lock',
    )

    def __init__(self, request_mtu, response_mtu, max_in_flight, config):
        if not isinstance(config, Config):
            raise TypeError('config must be a Config instance')
        self.request_mtu = request_mtu or DEFAULT_MAX_PACKET_SIZE
        self.response_mtu = response_mtu or DEFAULT_MAX_PACKET_SIZE
        if max_in_flight is None:
            max_in_flight = getattr(config, 'max_in_flight', 64)
        self.max_in_flight = max_in_flight
        self._requests = queue.Queue()
        self._responses = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._requests.put_nowait(None)
        except Exception:
            pass
        try:
            self._responses.put_nowait((None, None))
        except Exception:
            pass

    def is_closed(self):
        with self._lock:
            return self._closed
