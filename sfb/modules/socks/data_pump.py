# -*- coding: ascii -*-
"""
Shared SOCKS data pump helpers.

Each SOCKS connection uses two threads; each pump handles one socket/channel.
"""

from __future__ import absolute_import

import collections
import errno
import logging
import select
import socket

from ...logging_util import log_event
from ...channel import ChannelError
from ... import time_provider


def _get_socket_error(exc):
    err = getattr(exc, 'errno', None)
    if err is None:
        args = getattr(exc, 'args', None)
        if args:
            err = args[0]
    return err


def _is_would_block(exc):
    err = _get_socket_error(exc)
    if err is None:
        return False
    return err in (
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        getattr(errno, 'WSAEWOULDBLOCK', 10035),
    )


def _is_interrupted(exc):
    err = _get_socket_error(exc)
    return err == errno.EINTR


def _select(read_list, write_list, timeout):
    if not read_list and not write_list:
        if timeout:
            time_provider.sleep(timeout)
        return [], []
    try:
        rlist, wlist, _ = select.select(read_list, write_list, [], timeout)
        return rlist, wlist
    except select.error as exc:
        if _is_interrupted(exc):
            return [], []
        raise


def _pump_poll_bounds(config):
    base = config.non_blocking_poll_timeout
    if base is None or base <= 0:
        base = 0.01
    max_wait = config.socks_pump_backoff_max
    if max_wait is None or max_wait <= 0:
        max_wait = base
    if max_wait < base:
        max_wait = base
    return base, max_wait


def _shutdown_socket_write(sock):
    try:
        sock.shutdown(socket.SHUT_WR)
    except Exception:
        pass


def _outbound_cap(config):
    max_in_flight_bytes = config.max_in_flight * config.protocol_max_packet_size
    cap = config.channel_max_recv_buf
    if cap is None:
        cap = max_in_flight_bytes
    else:
        cap = max(cap, max_in_flight_bytes)
    if cap <= 0:
        cap = config.socks_relay_buffer_size
    return cap


def _log_pump_error(logger, rid, ch, side, direction, msg, exc):
    log_event(
        logger,
        logging.DEBUG,
        'sock.relay_error',
        msg,
        lambda: {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'error': str(exc),
            'side': side,
        },
    )


def _log_pump_start(logger, rid, ch, side, direction, label):
    log_event(
        logger,
        logging.DEBUG,
        'sock.pump_start',
        'SOCKS pump start',
        lambda: {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'label': label,
            'side': side,
        },
    )


def _log_pump_stop(logger, rid, ch, side, direction, label, reason,
                   error=None, stats=None):
    if reason is None:
        reason = 'unknown'

    def build_fields():
        fields = {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'label': label,
            'side': side,
            'reason': reason,
        }
        if error is not None:
            fields['error'] = str(error)
        if stats:
            fields.update(stats)
        return fields

    log_event(
        logger,
        logging.DEBUG,
        'sock.pump_stop',
        'SOCKS pump stop',
        build_fields,
    )


def pump_socket_to_channel(sock, channel, config, logger, stop_event,
                           rid, ch, side, recv_label, direction):
    """
    Pump data from a socket to a tunnel channel.

    Uses non-blocking writes with backpressure: stops reading from socket
    when channel buffer is full, which naturally backpressures TCP.
    """
    bytes_recv = 0
    bytes_written = 0
    buffer_full_count = 0
    wait_time = 0.0
    exit_reason = None
    exit_error = None
    fatal_error = False
    try:
        sock.setblocking(False)
        pending = None
        pending_offset = 0
        base_backoff, max_backoff = _pump_poll_bounds(config)
        backoff = base_backoff
        last_stats = time_provider.now()
        _log_pump_start(logger, rid, ch, side, direction, recv_label)
        while not stop_event.is_set():
            if pending is not None:
                try:
                    written = channel.write(pending[pending_offset:])
                    if written:
                        pending_offset += written
                        bytes_written += written
                    if pending_offset >= len(pending):
                        pending = None
                        pending_offset = 0
                        backoff = base_backoff
                        continue
                except ChannelError as exc:
                    if exc.code == 'buffer_full':
                        buffer_full_count += 1
                        start = time_provider.now()
                        try:
                            ready = channel.wait_send_space(timeout=backoff)
                        except ChannelError:
                            break
                        wait_time += time_provider.now() - start
                        if not ready:
                            backoff = min(backoff * 2.0, max_backoff)
                        else:
                            backoff = base_backoff
                        continue
                    if exc.code in ('not_open', 'closed'):
                        exit_reason = 'channel_closed'
                        break
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            'Channel write error', exc
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            'Channel write error', exc
                        )
                    break

            available = config.channel_max_send_buf - channel.send_buf_size
            if available <= 0:
                buffer_full_count += 1
                start = time_provider.now()
                try:
                    ready = channel.wait_send_space(timeout=backoff)
                except ChannelError:
                    break
                wait_time += time_provider.now() - start
                if not ready:
                    backoff = min(backoff * 2.0, max_backoff)
                else:
                    backoff = base_backoff
                continue

            rlist, _ = _select([sock], [], backoff)
            if not rlist:
                backoff = min(backoff * 2.0, max_backoff)
            else:
                backoff = base_backoff

            while rlist and not stop_event.is_set():
                available = config.channel_max_send_buf - channel.send_buf_size
                if available <= 0:
                    buffer_full_count += 1
                    break
                read_size = config.socks_relay_buffer_size
                if available < read_size:
                    read_size = available
                try:
                    data = sock.recv(read_size)
                except socket.error as exc:
                    if _is_would_block(exc):
                        break
                    fatal_error = True
                    exit_reason = 'socket_recv_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            '%s recv error' % recv_label, exc
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'socket_recv_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            '%s recv error' % recv_label, exc
                        )
                    break

                if not data:
                    exit_reason = 'socket_eof'
                    log_event(
                        logger,
                        logging.DEBUG,
                        'sock.relay_eof',
                        'Relay EOF',
                        lambda: {'rid': rid, 'ch': ch, 'label': recv_label, 'side': side},
                    )
                    channel.close()
                    return

                bytes_recv += len(data)
                try:
                    written = channel.write(data)
                    bytes_written += written
                    if written < len(data):
                        pending = data
                        pending_offset = written
                        break
                except ChannelError as exc:
                    if exc.code == 'buffer_full':
                        pending = data
                        pending_offset = 0
                        buffer_full_count += 1
                        break
                    if exc.code in ('not_open', 'closed'):
                        exit_reason = 'channel_closed'
                        return
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            'Channel write error', exc
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            'Channel write error', exc
                        )
                    break

            now = time_provider.now()
            if now - last_stats >= 1.0:
                log_event(
                    logger,
                    logging.DEBUG,
                    'sock.pump_stats',
                    'SOCKS pump stats',
                    lambda: {
                        'rid': rid,
                        'ch': ch,
                        'direction': direction,
                        'bytes_recv': bytes_recv,
                        'bytes_written': bytes_written,
                        'buffer_full': buffer_full_count,
                        'sleep_time': round(wait_time, 3),
                        'send_buf_size': channel.send_buf_size,
                        'side': side,
                    },
                )
                bytes_recv = 0
                bytes_written = 0
                buffer_full_count = 0
                wait_time = 0.0
                last_stats = now
        if exit_reason is None:
            if stop_event.is_set():
                exit_reason = 'stop_event'
            else:
                exit_reason = 'loop_exit'
    finally:
        if fatal_error:
            stop_event.set()
        _log_pump_stop(
            logger, rid, ch, side, direction, recv_label, exit_reason,
            error=exit_error,
            stats={
                'bytes_recv': bytes_recv,
                'bytes_written': bytes_written,
                'buffer_full': buffer_full_count,
                'sleep_time': round(wait_time, 3),
                'send_buf_size': channel.send_buf_size,
            },
        )


def pump_channel_to_socket(channel, sock, config, logger, stop_event,
                           rid, ch, side, send_label, direction):
    """
    Pump data from a tunnel channel to a socket.

    Uses non-blocking sends with select-driven backpressure.
    """
    bytes_read = 0
    bytes_sent = 0
    outbound_size = 0
    channel_closed = False
    channel_closed_reason = None
    exit_reason = None
    exit_error = None
    fatal_error = False
    try:
        sock.setblocking(False)
        outbound = collections.deque()
        outbound_offset = 0
        outbound_limit = _outbound_cap(config)
        last_stats = time_provider.now()
        base_backoff, max_backoff = _pump_poll_bounds(config)
        backoff = base_backoff
        write_timeout = config.socks_relay_write_timeout
        last_send = None
        _log_pump_start(logger, rid, ch, side, direction, send_label)
        while not stop_event.is_set():
            progress = False

            if outbound_size:
                now = time_provider.now()
                if last_send is None:
                    last_send = now
                if write_timeout is not None and now - last_send > write_timeout:
                    fatal_error = True
                    exit_reason = 'socket_send_timeout'
                    exit_error = socket.timeout()
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction,
                            '%s send timeout' % send_label, socket.timeout()
                        )
                    break
                _, wlist = _select([], [sock], backoff)
                if wlist:
                    chunk = outbound[0]
                    try:
                        sent = sock.send(chunk[outbound_offset:])
                    except socket.error as exc:
                        if _is_would_block(exc):
                            sent = 0
                        else:
                            fatal_error = True
                            exit_reason = 'socket_send_error'
                            exit_error = exc
                            if not stop_event.is_set():
                                _log_pump_error(
                                    logger, rid, ch, side, direction,
                                    '%s send error' % send_label, exc
                                )
                            break
                    except Exception as exc:
                        fatal_error = True
                        exit_reason = 'socket_send_error'
                        exit_error = exc
                        if not stop_event.is_set():
                            _log_pump_error(
                                logger, rid, ch, side, direction,
                                '%s send error' % send_label, exc
                            )
                        break

                    if sent:
                        bytes_sent += sent
                        last_send = time_provider.now()
                        progress = True
                        if sent < len(chunk) - outbound_offset:
                            outbound_offset += sent
                        else:
                            outbound.popleft()
                            outbound_size -= len(chunk)
                            outbound_offset = 0
                            if outbound_size == 0:
                                last_send = None

            if not channel_closed and outbound_size < outbound_limit:
                space = outbound_limit - outbound_size
                read_size = config.socks_relay_buffer_size
                if space < read_size:
                    read_size = space
                if read_size > 0:
                    if outbound_size:
                        read_timeout = 0.0
                    else:
                        read_timeout = min(config.socks_relay_channel_timeout, backoff)
                    try:
                        data = channel.read(read_size, timeout=read_timeout)
                    except Exception as exc:
                        if not stop_event.is_set():
                            _log_pump_error(
                                logger, rid, ch, side, direction,
                                'Channel read error', exc
                            )
                        fatal_error = True
                        exit_reason = 'channel_read_error'
                        exit_error = exc
                        break
                    if data is None:
                        pass
                    elif data == b'':
                        log_event(
                            logger,
                            logging.DEBUG,
                            'sock.relay_eof',
                            'Channel EOF',
                            lambda: {'rid': rid, 'ch': ch, 'side': side},
                        )
                        channel_closed = True
                        channel_closed_reason = 'channel_eof'
                        _shutdown_socket_write(sock)
                    else:
                        outbound.append(data)
                        outbound_size += len(data)
                        bytes_read += len(data)
                        progress = True
                        if last_send is None:
                            last_send = time_provider.now()

            if channel_closed and outbound_size == 0:
                if exit_reason is None:
                    exit_reason = channel_closed_reason or 'channel_eof'
                break

            if progress:
                backoff = base_backoff
            else:
                backoff = min(backoff * 2.0, max_backoff)

            now = time_provider.now()
            if now - last_stats >= 1.0:
                log_event(
                    logger,
                    logging.DEBUG,
                    'sock.pump_stats',
                    'SOCKS pump stats',
                    lambda: {
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
        if exit_reason is None:
            if stop_event.is_set():
                exit_reason = 'stop_event'
            elif channel_closed:
                exit_reason = channel_closed_reason or 'channel_eof'
            else:
                exit_reason = 'loop_exit'
    finally:
        if fatal_error:
            stop_event.set()
        _log_pump_stop(
            logger, rid, ch, side, direction, send_label, exit_reason,
            error=exit_error,
            stats={
                'bytes_read': bytes_read,
                'bytes_sent': bytes_sent,
                'outbound_size': outbound_size,
                'recv_buf_size': channel.recv_buf_size,
            },
        )
