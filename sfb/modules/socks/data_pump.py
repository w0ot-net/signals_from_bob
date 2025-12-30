# -*- coding: ascii -*-
"""
Shared SOCKS data pump helpers.
"""

from __future__ import absolute_import

import logging
import socket

from ...logging_util import log_event


def _log_pump_error(logger, rid, ch, side, direction, msg, exc):
    logger.debug('%s (rid=%d ch=%d): %s', msg, rid, ch, exc)
    log_event(
        logger,
        logging.DEBUG,
        'sock.relay_error',
        msg,
        {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'error': str(exc),
            'side': side,
        },
    )


def relay_socket_to_channel(sock, channel, config, logger, stop_event,
                            rid, ch, side, recv_label, direction):
    """
    Relay data from a socket to a tunnel channel.
    """
    try:
        sock.settimeout(config.socks_relay_socket_timeout)
        while not stop_event.is_set():
            try:
                data = sock.recv(config.socks_relay_buffer_size)
            except socket.timeout:
                continue
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        '%s recv error' % recv_label, exc
                    )
                break

            if not data:
                logger.debug('%s EOF (rid=%d ch=%d)', recv_label, rid, ch)
                break

            try:
                channel.write_all(data, timeout=config.socks_relay_write_timeout)
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        'Channel write error', exc
                    )
                break
    finally:
        stop_event.set()


def relay_channel_to_socket(channel, sock, config, logger, stop_event,
                            rid, ch, side, send_label, direction):
    """
    Relay data from a tunnel channel to a socket.
    """
    try:
        while not stop_event.is_set():
            try:
                data = channel.read(
                    config.socks_relay_buffer_size,
                    timeout=config.socks_relay_channel_timeout
                )
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        'Channel read error', exc
                    )
                break

            if data is None:
                continue
            if data == b'':
                logger.debug('Channel EOF (rid=%d ch=%d)', rid, ch)
                break

            try:
                sock.sendall(data)
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        '%s send error' % send_label, exc
                    )
                break
    finally:
        stop_event.set()
