# -*- coding: ascii -*-
"""
Shared SOCKS data pump helpers.
"""

from __future__ import absolute_import

import logging
import socket
import time

from ...logging_util import log_event
from ...channel import ChannelError


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


def pump_socket_to_channel(sock, channel, config, logger, stop_event,
                           rid, ch, side, recv_label, direction):
    """
    Pump data from a socket to a tunnel channel.

    Uses non-blocking writes with backpressure: stops reading from socket
    when channel buffer is full, which naturally backpressures TCP.
    """
    try:
        sock.settimeout(config.socks_relay_socket_timeout)
        pending = b''
        bytes_recv = 0
        bytes_written = 0
        buffer_full_count = 0
        sleep_time = 0.0
        last_stats = time.time()
        while not stop_event.is_set():
            try:
                if not pending:
                    pending = sock.recv(config.socks_relay_buffer_size)
            except socket.timeout:
                continue
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        '%s recv error' % recv_label, exc
                    )
                break

            if not pending:
                logger.debug('%s EOF (rid=%d ch=%d)', recv_label, rid, ch)
                break

            bytes_recv += len(pending)
            try:
                written = channel.write(pending)
                if written < len(pending):
                    pending = pending[written:]
                else:
                    pending = b''
                bytes_written += written
            except ChannelError as exc:
                if exc.code == 'buffer_full':
                    buffer_full_count += 1
                    sleep_time += 0.005
                    time.sleep(0.005)
                    continue
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        'Channel write error', exc
                    )
                break
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        'Channel write error', exc
                    )
                break

            now = time.time()
            if now - last_stats >= 1.0:
                log_event(
                    logger,
                    logging.DEBUG,
                    'sock.pump_stats',
                    'SOCKS pump stats',
                    {
                        'rid': rid,
                        'ch': ch,
                        'direction': direction,
                        'bytes_recv': bytes_recv,
                        'bytes_written': bytes_written,
                        'buffer_full': buffer_full_count,
                        'sleep_time': round(sleep_time, 3),
                        'send_buf_size': channel.send_buf_size,
                        'side': side,
                    },
                )
                bytes_recv = 0
                bytes_written = 0
                buffer_full_count = 0
                sleep_time = 0.0
                last_stats = now
    finally:
        stop_event.set()


def pump_channel_to_socket(channel, sock, config, logger, stop_event,
                           rid, ch, side, send_label, direction):
    """
    Pump data from a tunnel channel to a socket.

    TCP backpressure applies naturally via sendall blocking.
    """
    try:
        bytes_read = 0
        bytes_sent = 0
        last_stats = time.time()
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

            bytes_read += len(data)
            try:
                sock.sendall(data)
                bytes_sent += len(data)
            except Exception as exc:
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction,
                        '%s send error' % send_label, exc
                    )
                break

            now = time.time()
            if now - last_stats >= 1.0:
                log_event(
                    logger,
                    logging.DEBUG,
                    'sock.pump_stats',
                    'SOCKS pump stats',
                    {
                        'rid': rid,
                        'ch': ch,
                        'direction': direction,
                        'bytes_read': bytes_read,
                        'bytes_sent': bytes_sent,
                        'recv_buf_size': channel.recv_buf_size,
                        'side': side,
                    },
                )
                bytes_read = 0
                bytes_sent = 0
                last_stats = now
    finally:
        stop_event.set()
