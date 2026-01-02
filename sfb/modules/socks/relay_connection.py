# -*- coding: ascii -*-
"""
Shared SOCKS relay connection helper.
"""

from __future__ import absolute_import

import threading

from .data_pump import pump_channel_to_socket, pump_socket_to_channel


class RelayConnection(object):
    """Manages a bidirectional relay between a socket and a tunnel channel."""

    __slots__ = (
        'rid', 'ch', 'channel', 'sock', '_logger', '_config',
        '_stop_event', '_threads', '_thread_names', '_side', '_peer_label',
        '_socket_to_channel_label', '_channel_to_socket_label',
    )

    def __init__(self, rid, ch, channel, sock, logger, config, side, peer_label,
                 socket_to_channel_label, channel_to_socket_label,
                 thread_names=None):
        self.rid = rid
        self.ch = ch
        self.channel = channel
        self.sock = sock
        self._logger = logger
        self._config = config
        self._side = side
        self._peer_label = peer_label
        self._socket_to_channel_label = socket_to_channel_label
        self._channel_to_socket_label = channel_to_socket_label
        self._stop_event = threading.Event()
        self._threads = []
        self._thread_names = thread_names or (None, None)

    def start_relay(self):
        """Start bidirectional relay threads."""
        try:
            self.sock.setblocking(False)
        except Exception:
            pass

        t1 = threading.Thread(
            target=self._relay_socket_to_channel,
            name=self._thread_names[0],
        )
        t1.daemon = True

        t2 = threading.Thread(
            target=self._relay_channel_to_socket,
            name=self._thread_names[1],
        )
        t2.daemon = True

        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def _relay_socket_to_channel(self):
        """Relay data from socket to channel."""
        pump_socket_to_channel(
            self.sock,
            self.channel,
            self._config,
            self._logger,
            self._stop_event,
            self.rid,
            self.ch,
            self._side,
            self._peer_label,
            self._socket_to_channel_label,
            eof_callback=self.channel.close_write,
        )

    def _relay_channel_to_socket(self):
        """Relay data from channel to socket."""
        pump_channel_to_socket(
            self.channel,
            self.sock,
            self._config,
            self._logger,
            self._stop_event,
            self.rid,
            self.ch,
            self._side,
            self._peer_label,
            self._channel_to_socket_label,
        )

    def wait(self, timeout=None):
        """Wait for relay threads to complete."""
        for t in self._threads:
            t.join(timeout=timeout)

    def stop(self):
        """Signal relay to stop and close resources."""
        self._stop_event.set()

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass

        for t in self._threads:
            t.join(timeout=self._config.socks_thread_join_timeout)
