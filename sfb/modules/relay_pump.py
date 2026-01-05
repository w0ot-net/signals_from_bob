# -*- coding: ascii -*-
"""
Shared relay data pump helpers.

Each relay connection uses two threads; each pump handles one socket/channel.
"""

from __future__ import absolute_import

import collections
import errno
import logging
import select
import socket

from ..logging_util import log_event
from ..channel import ChannelError
from .. import time_provider
from .relay_logging import (
    add_field,
    add_fields,
    duration_secs,
    normalize_peer,
    relay_fields,
)


def _event_name(prefix, name):
    return '%s.%s' % (prefix, name)


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
    base = config.relay_pump_poll_timeout
    if base is None or base <= 0:
        base = 0.01
    max_wait = config.relay_pump_backoff_max
    if max_wait is None or max_wait <= 0:
        max_wait = base
    if max_wait < base:
        max_wait = base
    return base, max_wait


def _channel_state_snapshot(channel):
    return {
        'state': channel.state,
        'send_buf_size': channel.send_buf_size,
        'recv_buf_size': channel.recv_buf_size,
        'send_closed': getattr(channel, '_send_closed', None),
        'recv_closed': getattr(channel, '_recv_closed', None),
    }



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
        cap = config.relay_buffer_size
    return cap


def _notify_stop(stop_callback, fields):
    if stop_callback is None:
        return
    try:
        stop_callback(fields)
    except Exception:
        pass


def _log_pump_error(logger, rid, ch, side, direction, label, msg, exc,
                    event_prefix):
    peer = normalize_peer(label)
    log_event(
        logger,
        logging.DEBUG,
        _event_name(event_prefix, 'relay_error'),
        msg,
        lambda: {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'error': str(exc),
            'side': side,
            'label': label,
            'peer': peer,
        },
    )


def _log_pump_start(logger, rid, ch, side, direction, label, event_prefix):
    peer = normalize_peer(label)
    log_event(
        logger,
        logging.DEBUG,
        _event_name(event_prefix, 'pump_start'),
        'Relay pump start',
        lambda: {
            'rid': rid,
            'ch': ch,
            'direction': direction,
            'label': label,
            'side': side,
            'peer': peer,
        },
    )


def _log_pump_stop(logger, rid, ch, side, direction, label, reason,
                   stop_event=None, fatal=None, duration=None,
                   error=None, stats=None, stop_callback=None,
                   event_prefix='sock'):
    if reason is None:
        reason = 'unknown'
    peer = normalize_peer(label)

    def build_fields():
        fields = relay_fields(
            rid=rid,
            ch=ch,
            side=side,
            peer=peer,
            direction=direction,
            label=label,
        )
        fields['reason'] = reason
        add_field(fields, 'stop_event', stop_event)
        add_field(fields, 'fatal', fatal)
        add_field(fields, 'duration', duration)
        if error is not None:
            fields['error'] = str(error)
        if stats:
            fields.update(stats)
        return fields

    fields = None
    should_log = logger.isEnabledFor(logging.DEBUG)
    if stop_callback is not None or should_log:
        fields = build_fields()
    if stop_callback is not None:
        _notify_stop(stop_callback, fields)
    if should_log:
        log_event(
            logger,
            logging.DEBUG,
            _event_name(event_prefix, 'pump_stop'),
            'Relay pump stop',
            lambda: fields,
        )


def pump_socket_to_channel(sock, channel, config, logger, stop_event,
                           rid, ch, side, recv_label, direction,
                           eof_callback=None, stop_callback=None,
                           stats_enabled=True,
                           event_prefix='sock'):
    """
    Pump data from a socket to a tunnel channel.

    Uses non-blocking writes with backpressure: stops reading from socket
    when channel buffer is full, which naturally backpressures TCP.
    """
    stats_enabled = bool(stats_enabled)
    bytes_recv = 0
    bytes_written = 0
    total_bytes_recv = 0
    total_bytes_written = 0
    buffer_full_count = 0
    wait_time = 0.0
    select_wait_time = 0.0
    select_timeouts = 0
    exit_reason = None
    exit_error = None
    fatal_error = False
    start_time = time_provider.now()
    try:
        sock.setblocking(False)
        pending = None
        pending_offset = 0
        base_backoff, max_backoff = _pump_poll_bounds(config)
        backoff = base_backoff
        last_stats = time_provider.now() if stats_enabled else None
        _log_pump_start(
            logger, rid, ch, side, direction, recv_label, event_prefix
        )
        while not stop_event.is_set():
            if pending is not None:
                try:
                    written = channel.write(pending[pending_offset:])
                    if written:
                        pending_offset += written
                        if stats_enabled:
                            bytes_written += written
                            total_bytes_written += written
                    if pending_offset >= len(pending):
                        pending = None
                        pending_offset = 0
                        backoff = base_backoff
                        continue
                except ChannelError as exc:
                    if exc.code == 'buffer_full':
                        if stats_enabled:
                            buffer_full_count += 1
                            start = time_provider.now()
                        else:
                            start = None
                        try:
                            ready = channel.wait_send_space(timeout=backoff)
                        except ChannelError as exc:
                            if exc.code in ('not_open', 'closed', 'send_closed'):
                                exit_reason = 'channel_closed'
                            else:
                                exit_reason = 'channel_wait_error'
                                exit_error = exc
                                fatal_error = True
                            break
                        if stats_enabled and start is not None:
                            wait_time += time_provider.now() - start
                        if not ready:
                            backoff = min(backoff * 2.0, max_backoff)
                        else:
                            backoff = base_backoff
                        continue
                    if exc.code in ('not_open', 'closed', 'send_closed'):
                        exit_reason = 'channel_closed'
                        break
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, recv_label,
                            'Channel write error', exc, event_prefix
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, recv_label,
                            'Channel write error', exc, event_prefix
                        )
                    break

            available = config.channel_max_send_buf - channel.send_buf_size
            if available <= 0:
                if stats_enabled:
                    buffer_full_count += 1
                    start = time_provider.now()
                else:
                    start = None
                try:
                    ready = channel.wait_send_space(timeout=backoff)
                except ChannelError as exc:
                    if exc.code in ('not_open', 'closed', 'send_closed'):
                        exit_reason = 'channel_closed'
                    else:
                        exit_reason = 'channel_wait_error'
                        exit_error = exc
                        fatal_error = True
                    break
                if stats_enabled and start is not None:
                    wait_time += time_provider.now() - start
                if not ready:
                    backoff = min(backoff * 2.0, max_backoff)
                else:
                    backoff = base_backoff
                continue

            try:
                if stats_enabled:
                    select_start = time_provider.now()
                rlist, _ = _select([sock], [], backoff)
                if stats_enabled:
                    select_wait_time += time_provider.now() - select_start
            except Exception as exc:
                fatal_error = True
                exit_reason = 'socket_select_error'
                exit_error = exc
                if not stop_event.is_set():
                    _log_pump_error(
                        logger, rid, ch, side, direction, recv_label,
                        '%s select error' % recv_label, exc, event_prefix
                    )
                break
            if not rlist:
                if stats_enabled:
                    select_timeouts += 1
                backoff = min(backoff * 2.0, max_backoff)
            else:
                backoff = base_backoff

            while rlist and not stop_event.is_set():
                available = config.channel_max_send_buf - channel.send_buf_size
                if available <= 0:
                    if stats_enabled:
                        buffer_full_count += 1
                    break
                read_size = config.relay_buffer_size
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
                            logger, rid, ch, side, direction, recv_label,
                            '%s recv error' % recv_label, exc, event_prefix
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'socket_recv_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, recv_label,
                            '%s recv error' % recv_label, exc, event_prefix
                        )
                    break

                if not data:
                    exit_reason = 'socket_eof'
                    log_event(
                        logger,
                        logging.DEBUG,
                        _event_name(event_prefix, 'pump_socket_eof'),
                        'Relay pump socket EOF',
                        lambda: add_fields(relay_fields(
                            rid=rid,
                            ch=ch,
                            side=side,
                            peer=normalize_peer(recv_label),
                            direction=direction,
                            label=recv_label,
                        ), {
                            'source': 'socket',
                            'channel_state': _channel_state_snapshot(channel),
                        }),
                    )
                    log_event(
                        logger,
                        logging.DEBUG,
                        _event_name(event_prefix, 'relay_eof'),
                        'Relay EOF',
                        lambda: add_fields(relay_fields(
                            rid=rid,
                            ch=ch,
                            side=side,
                            peer=normalize_peer(recv_label),
                            direction=direction,
                            label=recv_label,
                        ), {'source': 'socket'}),
                    )
                    if eof_callback is not None:
                        if not stop_event.is_set():
                            log_event(
                                logger,
                                logging.DEBUG,
                                _event_name(event_prefix, 'relay_half_close_request'),
                                'Relay half-close requested',
                                lambda: add_fields(relay_fields(
                                    rid=rid,
                                    ch=ch,
                                    side=side,
                                    peer=normalize_peer(recv_label),
                                    direction=direction,
                                    label=recv_label,
                                ), {'channel_state': _channel_state_snapshot(channel)}),
                            )
                            try:
                                eof_callback()
                            except Exception as exc:
                                _log_pump_error(
                                    logger, rid, ch, side, direction, recv_label,
                                    'Half-close callback error', exc, event_prefix
                                )
                    return

                if stats_enabled:
                    bytes_recv += len(data)
                    total_bytes_recv += len(data)
                try:
                    written = channel.write(data)
                    if stats_enabled:
                        bytes_written += written
                        total_bytes_written += written
                    if written < len(data):
                        pending = data
                        pending_offset = written
                        break
                except ChannelError as exc:
                    if exc.code == 'buffer_full':
                        pending = data
                        pending_offset = 0
                        if stats_enabled:
                            buffer_full_count += 1
                        break
                    if exc.code in ('not_open', 'closed', 'send_closed'):
                        exit_reason = 'channel_closed'
                        return
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, recv_label,
                            'Channel write error', exc, event_prefix
                        )
                    break
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, recv_label,
                            'Channel write error', exc, event_prefix
                        )
                    break

            if stats_enabled:
                now = time_provider.now()
                if now - last_stats >= 1.0:
                    log_event(
                        logger,
                        logging.DEBUG,
                        _event_name(event_prefix, 'pump_stats'),
                        'Relay pump stats',
                        lambda: add_fields(relay_fields(
                            rid=rid,
                            ch=ch,
                            side=side,
                            peer=normalize_peer(recv_label),
                            direction=direction,
                            label=recv_label,
                        ), {
                            'bytes_in': bytes_recv,
                            'bytes_out': bytes_written,
                            'bytes_recv': bytes_recv,
                            'bytes_written': bytes_written,
                            'buffer_full': buffer_full_count,
                            'sleep_time': round(wait_time, 3),
                            'wait_time': round(wait_time, 3),
                            'select_wait_time': round(select_wait_time, 3),
                            'select_timeouts': select_timeouts,
                            'send_buf_size': channel.send_buf_size,
                            'recv_buf_size': channel.recv_buf_size,
                            'backoff': round(backoff, 3),
                        }),
                    )
                    bytes_recv = 0
                    bytes_written = 0
                    buffer_full_count = 0
                    wait_time = 0.0
                    select_wait_time = 0.0
                    select_timeouts = 0
                    last_stats = now
        if exit_reason is None:
            if stop_event.is_set():
                exit_reason = 'stop_event'
            else:
                exit_reason = 'loop_exit'
    finally:
        if fatal_error:
            stop_event.set()
        if exit_reason == 'stop_event':
            log_event(
                logger,
                logging.DEBUG,
                _event_name(event_prefix, 'pump_stop_event'),
                'Relay pump stop event',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side=side,
                    peer=normalize_peer(recv_label),
                    direction=direction,
                    label=recv_label,
                ), {'duration': duration_secs(start_time), 'stop_event': True}),
            )
        stop_stats = None
        if stats_enabled:
            stop_stats = {
                'bytes_in': bytes_recv,
                'bytes_out': bytes_written,
                'bytes_recv': bytes_recv,
                'bytes_written': bytes_written,
                'bytes_in_total': total_bytes_recv,
                'bytes_out_total': total_bytes_written,
                'bytes_recv_total': total_bytes_recv,
                'bytes_written_total': total_bytes_written,
                'buffer_full': buffer_full_count,
                'sleep_time': round(wait_time, 3),
                'wait_time': round(wait_time, 3),
                'select_wait_time': round(select_wait_time, 3),
                'select_timeouts': select_timeouts,
                'send_buf_size': channel.send_buf_size,
                'recv_buf_size': channel.recv_buf_size,
                'backoff': round(backoff, 3),
            }
        _log_pump_stop(
            logger, rid, ch, side, direction, recv_label, exit_reason,
            stop_event=stop_event.is_set(),
            fatal=fatal_error,
            duration=duration_secs(start_time),
            error=exit_error,
            stats=stop_stats,
            stop_callback=stop_callback,
            event_prefix=event_prefix,
        )


def pump_channel_to_socket(channel, sock, config, logger, stop_event,
                           rid, ch, side, send_label, direction,
                           stop_callback=None, stats_enabled=True,
                           event_prefix='sock'):
    """
    Pump data from a tunnel channel to a socket.

    Uses non-blocking sends with select-driven backpressure.
    """
    stats_enabled = bool(stats_enabled)
    bytes_read = 0
    bytes_sent = 0
    total_bytes_read = 0
    total_bytes_sent = 0
    outbound_size = 0
    read_wait_time = 0.0
    channel_timeouts = 0
    select_wait_time = 0.0
    select_timeouts = 0
    channel_closed = False
    channel_closed_reason = None
    shutdown_pending = False
    exit_reason = None
    exit_error = None
    fatal_error = False
    start_time = time_provider.now()
    try:
        sock.setblocking(False)
        outbound = collections.deque()
        outbound_offset = 0
        outbound_limit = _outbound_cap(config)
        last_stats = time_provider.now() if stats_enabled else None
        base_backoff, max_backoff = _pump_poll_bounds(config)
        backoff = base_backoff
        write_timeout = config.relay_write_timeout
        last_send = None
        _log_pump_start(
            logger, rid, ch, side, direction, send_label, event_prefix
        )
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
                        log_event(
                            logger,
                            logging.DEBUG,
                            _event_name(event_prefix, 'pump_timeout'),
                            'Relay pump socket send timeout',
                            lambda: add_fields(relay_fields(
                                rid=rid,
                                ch=ch,
                                side=side,
                                peer=normalize_peer(send_label),
                                direction=direction,
                                label=send_label,
                            ), {
                                'timeout': write_timeout,
                                'elapsed': round(now - last_send, 3),
                                'kind': 'socket_send',
                            }),
                        )
                        _log_pump_error(
                            logger, rid, ch, side, direction, send_label,
                            '%s send timeout' % send_label, socket.timeout(),
                            event_prefix
                        )
                    break
                try:
                    if stats_enabled:
                        select_start = time_provider.now()
                    _, wlist = _select([], [sock], backoff)
                    if stats_enabled:
                        select_wait_time += time_provider.now() - select_start
                except Exception as exc:
                    fatal_error = True
                    exit_reason = 'socket_select_error'
                    exit_error = exc
                    if not stop_event.is_set():
                        _log_pump_error(
                            logger, rid, ch, side, direction, send_label,
                            '%s select error' % send_label, exc, event_prefix
                        )
                    break
                if not wlist:
                    if stats_enabled:
                        select_timeouts += 1
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
                                    logger, rid, ch, side, direction, send_label,
                                    '%s send error' % send_label, exc, event_prefix
                                )
                            break
                    except Exception as exc:
                        fatal_error = True
                        exit_reason = 'socket_send_error'
                        exit_error = exc
                        if not stop_event.is_set():
                            _log_pump_error(
                                logger, rid, ch, side, direction, send_label,
                                '%s send error' % send_label, exc, event_prefix
                            )
                        break

                    if sent:
                        if stats_enabled:
                            bytes_sent += sent
                            total_bytes_sent += sent
                        outbound_size -= sent
                        if outbound_size < 0:
                            outbound_size = 0
                        last_send = time_provider.now()
                        progress = True
                        if sent < len(chunk) - outbound_offset:
                            outbound_offset += sent
                        else:
                            outbound.popleft()
                            outbound_offset = 0
                            if outbound_size == 0:
                                last_send = None

            if not channel_closed and outbound_size < outbound_limit:
                space = outbound_limit - outbound_size
                read_size = config.relay_buffer_size
                if space < read_size:
                    read_size = space
                if read_size > 0:
                    if outbound_size:
                        read_timeout = 0.0
                    else:
                        read_timeout = min(config.relay_channel_timeout, backoff)
                    try:
                        if stats_enabled:
                            read_start = time_provider.now()
                        data = channel.read(read_size, timeout=read_timeout)
                        if stats_enabled:
                            read_wait_time += time_provider.now() - read_start
                    except Exception as exc:
                        if not stop_event.is_set():
                            _log_pump_error(
                                logger, rid, ch, side, direction, send_label,
                                'Channel read error', exc, event_prefix
                            )
                        fatal_error = True
                        exit_reason = 'channel_read_error'
                        exit_error = exc
                        break
                    if data is None:
                        if stats_enabled:
                            channel_timeouts += 1
                        pass
                    elif data == b'':
                        log_event(
                            logger,
                            logging.DEBUG,
                            _event_name(event_prefix, 'pump_channel_eof'),
                            'Relay pump channel EOF',
                            lambda: add_fields(relay_fields(
                                rid=rid,
                                ch=ch,
                                side=side,
                                peer=normalize_peer(send_label),
                                direction=direction,
                                label=send_label,
                            ), {
                                'source': 'channel',
                                'channel_state': _channel_state_snapshot(channel),
                            }),
                        )
                        log_event(
                            logger,
                            logging.DEBUG,
                            _event_name(event_prefix, 'relay_eof'),
                            'Channel EOF',
                            lambda: add_fields(relay_fields(
                                rid=rid,
                                ch=ch,
                                side=side,
                                peer=normalize_peer(send_label),
                                direction=direction,
                                label=send_label,
                            ), {'source': 'channel'}),
                        )
                        channel_closed = True
                        if channel.is_closed:
                            channel_closed_reason = 'channel_eof'
                        else:
                            channel_closed_reason = 'remote_half_close'
                        shutdown_pending = True
                    else:
                        outbound.append(data)
                        outbound_size += len(data)
                        if stats_enabled:
                            bytes_read += len(data)
                            total_bytes_read += len(data)
                        progress = True
                        if last_send is None:
                            last_send = time_provider.now()

            if channel_closed and outbound_size == 0:
                if shutdown_pending:
                    log_event(
                        logger,
                        logging.DEBUG,
                        _event_name(event_prefix, 'relay_shutdown_write'),
                        'Relay socket write shutdown',
                        lambda: add_fields(relay_fields(
                            rid=rid,
                            ch=ch,
                            side=side,
                            peer=normalize_peer(send_label),
                            direction=direction,
                            label=send_label,
                        ), {
                            'reason': channel_closed_reason,
                            'outbound_size': outbound_size,
                            'channel_state': _channel_state_snapshot(channel),
                        }),
                    )
                    _shutdown_socket_write(sock)
                    shutdown_pending = False
                if exit_reason is None:
                    exit_reason = channel_closed_reason or 'channel_eof'
                break

            if progress:
                backoff = base_backoff
            else:
                backoff = min(backoff * 2.0, max_backoff)

            if stats_enabled:
                now = time_provider.now()
                if now - last_stats >= 1.0:
                    log_event(
                        logger,
                        logging.DEBUG,
                        _event_name(event_prefix, 'pump_stats'),
                        'Relay pump stats',
                        lambda: add_fields(relay_fields(
                            rid=rid,
                            ch=ch,
                            side=side,
                            peer=normalize_peer(send_label),
                            direction=direction,
                            label=send_label,
                        ), {
                            'bytes_in': bytes_read,
                            'bytes_out': bytes_sent,
                            'bytes_read': bytes_read,
                            'bytes_sent': bytes_sent,
                            'outbound_size': outbound_size,
                            'wait_time': round(read_wait_time, 3),
                            'channel_timeouts': channel_timeouts,
                            'select_wait_time': round(select_wait_time, 3),
                            'select_timeouts': select_timeouts,
                            'send_buf_size': channel.send_buf_size,
                            'recv_buf_size': channel.recv_buf_size,
                            'backoff': round(backoff, 3),
                        }),
                    )
                    bytes_read = 0
                    bytes_sent = 0
                    read_wait_time = 0.0
                    channel_timeouts = 0
                    select_wait_time = 0.0
                    select_timeouts = 0
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
        if exit_reason == 'stop_event':
            log_event(
                logger,
                logging.DEBUG,
                _event_name(event_prefix, 'pump_stop_event'),
                'Relay pump stop event',
                lambda: add_fields(relay_fields(
                    rid=rid,
                    ch=ch,
                    side=side,
                    peer=normalize_peer(send_label),
                    direction=direction,
                    label=send_label,
                ), {'duration': duration_secs(start_time), 'stop_event': True}),
            )
        stop_stats = None
        if stats_enabled:
            stop_stats = {
                'bytes_in': bytes_read,
                'bytes_out': bytes_sent,
                'bytes_read': bytes_read,
                'bytes_sent': bytes_sent,
                'bytes_in_total': total_bytes_read,
                'bytes_out_total': total_bytes_sent,
                'bytes_read_total': total_bytes_read,
                'bytes_sent_total': total_bytes_sent,
                'outbound_size': outbound_size,
                'wait_time': round(read_wait_time, 3),
                'channel_timeouts': channel_timeouts,
                'select_wait_time': round(select_wait_time, 3),
                'select_timeouts': select_timeouts,
                'send_buf_size': channel.send_buf_size,
                'recv_buf_size': channel.recv_buf_size,
                'backoff': round(backoff, 3),
            }
        _log_pump_stop(
            logger, rid, ch, side, direction, send_label, exit_reason,
            stop_event=stop_event.is_set(),
            fatal=fatal_error,
            duration=duration_secs(start_time),
            error=exit_error,
            stats=stop_stats,
            stop_callback=stop_callback,
            event_prefix=event_prefix,
        )
