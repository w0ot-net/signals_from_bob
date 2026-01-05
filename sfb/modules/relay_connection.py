# -*- coding: ascii -*-
"""
Shared relay connection helper.
"""

from __future__ import absolute_import

import logging
import threading

from ..logging_util import log_event
from .. import time_provider
from .relay_pump import pump_channel_to_socket, pump_socket_to_channel
from .relay_logging import (
    add_fields,
    duration_secs,
    normalize_peer,
    relay_fields,
)


def _event_name(prefix, name):
    return '%s.%s' % (prefix, name)


class RelayConnection(object):
    """Manages a bidirectional relay between a socket and a tunnel channel."""

    __slots__ = (
        'rid', 'ch', 'channel', 'sock', '_logger', '_config',
        '_stop_event', '_threads', '_thread_names', '_side', '_peer_label',
        '_socket_to_channel_label', '_channel_to_socket_label',
        '_start_time', '_stop_logged', '_pump_info', '_pump_lock',
        '_event_prefix',
    )

    def __init__(self, rid, ch, channel, sock, logger, config, side, peer_label,
                 socket_to_channel_label, channel_to_socket_label,
                 thread_names=None, event_prefix='sock'):
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
        self._event_prefix = event_prefix
        self._start_time = None
        self._stop_logged = False
        self._pump_info = {}
        self._pump_lock = threading.Lock()

    def start_relay(self):
        """Start bidirectional relay threads."""
        try:
            self.sock.setblocking(False)
        except Exception:
            pass

        if self._start_time is None:
            self._start_time = time_provider.now()

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
        self._log_start()

    def _log_start(self):
        peer = normalize_peer(self._peer_label)
        log_event(
            self._logger,
            logging.INFO,
            _event_name(self._event_prefix, 'relay_start'),
            'Relay start',
            lambda: add_fields(relay_fields(
                rid=self.rid,
                ch=self.ch,
                side=self._side,
                peer=peer,
                label=self._peer_label,
            ), {
                'threads': list(self._thread_names),
                'direction_in': self._socket_to_channel_label,
                'direction_out': self._channel_to_socket_label,
            }),
        )

    def _on_pump_stop(self, info):
        if not info:
            return
        direction = info.get('direction')
        if not direction:
            return
        with self._pump_lock:
            self._pump_info[direction] = dict(info)

    def _collect_pump_info(self):
        with self._pump_lock:
            return dict(self._pump_info)

    def _build_summary(self):
        pump_info = self._collect_pump_info()
        pump_reasons = {}
        pump_errors = {}
        pump_durations = {}
        fatal_error = False
        stop_event_seen = False
        for direction, info in pump_info.items():
            reason = info.get('reason')
            if reason:
                pump_reasons[direction] = reason
            error = info.get('error')
            if error:
                pump_errors[direction] = error
            duration = info.get('duration')
            if duration is not None:
                pump_durations[direction] = duration
            if info.get('fatal'):
                fatal_error = True
            if info.get('stop_event'):
                stop_event_seen = True

        socket_to_channel = pump_info.get(self._socket_to_channel_label, {})
        channel_to_socket = pump_info.get(self._channel_to_socket_label, {})

        stop_cause = 'loop_exit'
        eof_reasons = (
            'socket_eof',
            'channel_eof',
            'remote_half_close',
            'channel_closed',
        )
        if fatal_error:
            stop_cause = 'error'
        elif stop_event_seen:
            stop_cause = 'stop_event'
        else:
            for reason in pump_reasons.values():
                if reason in eof_reasons:
                    stop_cause = 'eof'
                    break

        summary = {}
        if pump_reasons:
            summary['pump_reasons'] = pump_reasons
        if pump_errors:
            summary['pump_errors'] = pump_errors
        if pump_durations:
            summary['pump_durations'] = pump_durations
        add_fields(summary, {
            'bytes_from_peer': socket_to_channel.get('bytes_in_total'),
            'bytes_to_peer': channel_to_socket.get('bytes_out_total'),
            'bytes_to_channel': socket_to_channel.get('bytes_out_total'),
            'bytes_from_channel': channel_to_socket.get('bytes_in_total'),
            'stop_event': bool(self._stop_event.is_set() or stop_event_seen),
            'stop_cause': stop_cause,
            'clean_shutdown': not fatal_error,
        })
        add_fields(summary, {
            'duration': duration_secs(self._start_time),
        })
        return summary

    def get_summary(self):
        """Return a dict summary of relay lifecycle and bytes moved."""
        return self._build_summary()

    def _log_stop(self):
        if self._stop_logged or self._start_time is None:
            return
        self._stop_logged = True
        summary = self._build_summary()
        peer = normalize_peer(self._peer_label)
        log_event(
            self._logger,
            logging.INFO,
            _event_name(self._event_prefix, 'relay_stop'),
            'Relay stop',
            lambda: add_fields(relay_fields(
                rid=self.rid,
                ch=self.ch,
                side=self._side,
                peer=peer,
                label=self._peer_label,
            ), summary),
        )

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
            stop_callback=self._on_pump_stop,
            stats_enabled=self._config.stats_enabled,
            event_prefix=self._event_prefix,
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
            stop_callback=self._on_pump_stop,
            stats_enabled=self._config.stats_enabled,
            event_prefix=self._event_prefix,
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
            t.join(timeout=self._config.relay_thread_join_timeout)
        self._log_stop()
