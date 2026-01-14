# -*- coding: ascii -*-
"""NC Linux data pump helpers."""

from __future__ import absolute_import

import errno
import logging
import os
import select
import socket

from ...channel import ChannelError
from ...logging_util import log_event
from ... import time_provider


def _get_errno(exc):
    err = getattr(exc, 'errno', None)
    if err is None:
        args = getattr(exc, 'args', None)
        if args:
            err = args[0]
    return err


def _is_would_block(exc):
    err = _get_errno(exc)
    if err is None:
        return False
    return err in (
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        getattr(errno, 'WSAEWOULDBLOCK', 10035),
    )


def _is_interrupted(exc):
    err = _get_errno(exc)
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


def _shutdown_fd_write(fd):
    try:
        sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
    except Exception:
        return False
    try:
        sock.shutdown(socket.SHUT_WR)
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _poll_bounds(config):
    base = getattr(config, 'nc_linux_poll_timeout', 0.01)
    if base is None or base <= 0:
        base = 0.01
    max_wait = base * 8.0
    return base, max_wait


def _log_pump_start(logger, rid, ch, side, direction, label):
    if logger.isEnabledFor(logging.DEBUG):
        log_event(
            logger,
            logging.DEBUG,
            'nc.pump_start',
            'NC pump start',
            lambda: {
                'rid': rid,
                'ch': ch,
                'side': side,
                'direction': direction,
                'label': label,
            },
        )


def _log_pump_stop(logger, rid, ch, side, direction, label, reason,
                   stop_event=None, fatal=None, duration=None,
                   error=None, stats=None):
    if reason is None:
        reason = 'unknown'

    def build_fields():
        fields = {
            'rid': rid,
            'ch': ch,
            'side': side,
            'direction': direction,
            'label': label,
            'reason': reason,
        }
        if stop_event is not None:
            fields['stop_event'] = bool(stop_event)
        if fatal is not None:
            fields['fatal'] = bool(fatal)
        if duration is not None:
            fields['duration'] = duration
        if error is not None:
            fields['error'] = str(error)
        if stats:
            fields.update(stats)
        return fields

    if logger.isEnabledFor(logging.DEBUG):
        log_event(
            logger,
            logging.DEBUG,
            'nc.pump_stop',
            'NC pump stop',
            lambda: build_fields(),
        )


def pump_fd_to_channel(fd, channel, config, logger, stop_event,
                       rid, ch, side, label, eof_callback=None,
                       stop_callback=None, stats_enabled=True):
    """
    Pump data from a file descriptor to a tunnel channel.
    """
    stats_enabled = bool(stats_enabled)
    bytes_read = 0
    bytes_written = 0
    total_read = 0
    total_written = 0
    wait_time = 0.0
    buffer_full_count = 0
    exit_reason = None
    exit_error = None
    fatal_error = False
    start_time = time_provider.now()

    base_backoff, max_backoff = _poll_bounds(config)
    backoff = base_backoff
    pending = None
    pending_offset = 0

    _log_pump_start(logger, rid, ch, side, 'fd_to_channel', label)

    try:
        while not stop_event.is_set():
            if pending is not None:
                try:
                    written = channel.write(pending[pending_offset:])
                    if written:
                        pending_offset += written
                        if stats_enabled:
                            bytes_written += written
                            total_written += written
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
                    exit_reason = 'channel_write_error'
                    exit_error = exc
                    fatal_error = True
                    break

            rlist, _ = _select([fd], [], backoff)
            if not rlist:
                continue
            try:
                data = os.read(fd, config.nc_linux_buffer_size)
            except OSError as exc:
                if _is_interrupted(exc) or _is_would_block(exc):
                    continue
                exit_reason = 'fd_read_error'
                exit_error = exc
                fatal_error = True
                break
            if not data:
                exit_reason = 'fd_eof'
                if eof_callback is not None:
                    try:
                        eof_callback()
                    except Exception:
                        pass
                break
            pending = data
            pending_offset = 0
            if stats_enabled:
                bytes_read += len(data)
                total_read += len(data)

        if exit_reason is None:
            exit_reason = 'stop_event' if stop_event.is_set() else 'loop_exit'
    finally:
        if fatal_error or exit_reason == 'channel_closed':
            stop_event.set()
        stop_stats = None
        if stats_enabled:
            stop_stats = {
                'bytes_in': bytes_read,
                'bytes_out': bytes_written,
                'bytes_in_total': total_read,
                'bytes_out_total': total_written,
                'buffer_full': buffer_full_count,
                'wait_time': round(wait_time, 3),
            }
        _log_pump_stop(
            logger,
            rid,
            ch,
            side,
            'fd_to_channel',
            label,
            exit_reason,
            stop_event=stop_event.is_set(),
            fatal=fatal_error,
            duration=round(time_provider.now() - start_time, 3),
            error=exit_error,
            stats=stop_stats,
        )
        if stop_callback is not None:
            stop_callback({
                'direction': 'fd_to_channel',
                'reason': exit_reason,
                'fatal': fatal_error,
            })



def pump_channel_to_fd(channel, fd, config, logger, stop_event,
                       rid, ch, side, label, stop_callback=None,
                       stats_enabled=True):
    """
    Pump data from a tunnel channel to a file descriptor.
    """
    stats_enabled = bool(stats_enabled)
    bytes_read = 0
    bytes_written = 0
    total_read = 0
    total_written = 0
    wait_time = 0.0
    select_timeouts = 0
    exit_reason = None
    exit_error = None
    fatal_error = False
    start_time = time_provider.now()

    base_backoff, max_backoff = _poll_bounds(config)
    backoff = base_backoff
    pending = None
    pending_offset = 0
    recv_seq = channel._get_recv_seq()

    _log_pump_start(logger, rid, ch, side, 'channel_to_fd', label)

    try:
        while not stop_event.is_set():
            if pending is not None:
                try:
                    written = os.write(fd, pending[pending_offset:])
                    if written:
                        pending_offset += written
                        if stats_enabled:
                            bytes_written += written
                            total_written += written
                    if pending_offset >= len(pending):
                        pending = None
                        pending_offset = 0
                        backoff = base_backoff
                    continue
                except OSError as exc:
                    if _is_interrupted(exc) or _is_would_block(exc):
                        _, wlist = _select([], [fd], backoff)
                        if not wlist:
                            if stats_enabled:
                                select_timeouts += 1
                            backoff = min(backoff * 2.0, max_backoff)
                        else:
                            backoff = base_backoff
                        continue
                    exit_reason = 'fd_write_error'
                    exit_error = exc
                    fatal_error = True
                    break

            try:
                next_seq = channel.wait_recv_seq(recv_seq, timeout=backoff)
                if next_seq is None:
                    data = None
                else:
                    recv_seq = next_seq
                    data = channel.read(config.nc_linux_buffer_size, timeout=0)
            except ChannelError as exc:
                exit_reason = 'channel_read_error'
                exit_error = exc
                fatal_error = True
                break

            if data is None:
                if stats_enabled:
                    wait_time += backoff
                continue
            if data == b'':
                if channel.is_closed:
                    exit_reason = 'channel_closed'
                else:
                    _shutdown_fd_write(fd)
                    exit_reason = 'remote_half_close'
                break

            pending = data
            pending_offset = 0
            if stats_enabled:
                bytes_read += len(data)
                total_read += len(data)

        if exit_reason is None:
            exit_reason = 'stop_event' if stop_event.is_set() else 'loop_exit'
    finally:
        if fatal_error or exit_reason == 'channel_closed':
            stop_event.set()
        stop_stats = None
        if stats_enabled:
            stop_stats = {
                'bytes_in': bytes_read,
                'bytes_out': bytes_written,
                'bytes_in_total': total_read,
                'bytes_out_total': total_written,
                'wait_time': round(wait_time, 3),
                'select_timeouts': select_timeouts,
            }
        _log_pump_stop(
            logger,
            rid,
            ch,
            side,
            'channel_to_fd',
            label,
            exit_reason,
            stop_event=stop_event.is_set(),
            fatal=fatal_error,
            duration=round(time_provider.now() - start_time, 3),
            error=exit_error,
            stats=stop_stats,
        )
        if stop_callback is not None:
            stop_callback({
                'direction': 'channel_to_fd',
                'reason': exit_reason,
                'fatal': fatal_error,
            })
