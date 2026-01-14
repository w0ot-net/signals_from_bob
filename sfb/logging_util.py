# -*- coding: ascii -*-
"""
Logging helpers for the tunnel codebase.
"""

from __future__ import absolute_import

import fnmatch
import logging
import sys
import threading
import sqlite3
import json

from .compat import queue

from .compat import text_type
from . import time_provider


DEFAULT_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
_SQLITE_INT64_MIN = -9223372036854775808
_SQLITE_INT64_MAX = 9223372036854775807


class StructuredLogFormatter(logging.Formatter):
    """
    Formatter that appends event fields when present.
    """

    def __init__(self, fmt=None, datefmt=None, max_line_length=None,
                 include_message=True, label_event=True, label_fields=True):
        logging.Formatter.__init__(self, fmt=fmt, datefmt=datefmt)
        self._max_line_length = max_line_length
        self._include_message = bool(include_message)
        self._label_event = bool(label_event)
        self._label_fields = bool(label_fields)

    def format(self, record):
        message = logging.Formatter.format(self, record)
        event = getattr(record, 'event', None)
        fields = _resolve_fields(record)
        extras = []
        if event:
            event_text = _coerce_text(event)
            if self._label_event:
                extras.append('event=%s' % event_text)
            else:
                extras.append(event_text)
        if fields:
            fields_text = _encode_fields(fields)
            if self._label_fields:
                extras.append('fields=%s' % fields_text)
            else:
                extras.append(fields_text)
        if extras:
            if self._include_message:
                message = '%s | %s' % (message, ' '.join(extras))
            else:
                message = ' '.join(extras)
        if self._max_line_length:
            return _truncate_lines(message, self._max_line_length)
        return message


def configure_logging(level='INFO', to_stdout=True, log_file=None):
    """
    Configure tunnel logging.
    """
    logger = logging.getLogger('sfb')
    logger.setLevel(_coerce_level(level))
    formatter = logging.Formatter(DEFAULT_FORMAT)

    if to_stdout:
        _ensure_handler(logger, logging.StreamHandler, formatter, sys.stdout)

    if log_file:
        _ensure_handler(logger, logging.FileHandler, formatter, log_file)


def log_event(logger, level, event, message, fields, **kwargs):
    """
    Emit a structured log event.

    Args:
        fields: callable returning a dict or None
    """
    if not logger.isEnabledFor(level):
        return
    if not callable(fields):
        raise TypeError('fields must be callable')
    extra = {
        'event': event,
        'fields': fields,
    }
    logger.log(level, message, extra=extra, **kwargs)


def add_sqlite_handler(logger, db_path, level=None, formatter=None,
                       flush_interval=0.5, queue_maxsize=0):
    """
    Attach SQLite log handler to a logger if not already present.
    """
    for handler in logger.handlers:
        if isinstance(handler, SQLiteLogHandler):
            return handler
    handler = SQLiteLogHandler(
        db_path=db_path,
        flush_interval=flush_interval,
        queue_maxsize=queue_maxsize,
    )
    if level is not None:
        handler.setLevel(_coerce_level(level))
    if formatter is None:
        formatter = logging.Formatter(DEFAULT_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return handler


class ComponentFilter(logging.Filter):
    """
    Filter log records by component using config toggles.
    """

    def __init__(self, config):
        logging.Filter.__init__(self)
        self._event_whitelist = _normalize_patterns(
            getattr(config, 'log_event_whitelist', ())
        )
        self._event_blacklist = _normalize_patterns(
            getattr(config, 'log_event_blacklist', ())
        )
        self._dns_enabled = bool(getattr(config, 'log_component_transport_dns', True))
        self._dns_event_prefixes = ('dns.', 'dns_txt.')
        self._dns_logger_prefixes = (
            'sfb.transport.dns.',
            'tunnel.sfb.transport.dns.',
            'sfb.transport.dns',
            'tunnel.sfb.transport.dns',
            'sfb.transport.dns_txt.',
            'tunnel.sfb.transport.dns_txt.',
            'sfb.transport.dns_txt',
            'tunnel.sfb.transport.dns_txt',
        )
        self._icmp_enabled = bool(getattr(config, 'log_component_transport_icmp', True))
        self._icmp_logger_prefixes = (
            'sfb.transport.icmp.',
            'sfb.transport.icmp',
        )
        self._tls_enabled = bool(getattr(config, 'log_component_transport_tls', True))
        self._tls_event_prefix = 'tls.'
        self._tls_logger_prefixes = (
            'sfb.transport.tls_handshake.',
            'sfb.transport.tls_handshake',
            'sfb.transport.tls_handshake_bump.',
            'sfb.transport.tls_handshake_bump',
        )
        self._tunnel_enabled = bool(getattr(config, 'log_component_tunnel', True))
        self._tunnel_event_prefix = 'tunnel.'
        self._tunnel_logger_prefixes = (
            'sfb.tunnel.',
            'tunnel.sfb.tunnel.',
            'sfb.tunnel',
            'tunnel.sfb.tunnel',
        )
        self._channel_enabled = bool(getattr(config, 'log_component_channel', True))
        self._channel_event_prefix = 'channel.'
        self._channel_logger_prefixes = (
            'sfb.channel.',
            'sfb.channel',
        )
        self._protocol_enabled = bool(getattr(config, 'log_component_protocol', True))
        self._protocol_logger_prefixes = (
            'sfb.protocol.',
            'sfb.protocol',
            'tunnel.sfb.protocol.',
            'tunnel.sfb.protocol',
        )
        self._module_relay_enabled = bool(getattr(config, 'log_component_module_relay', True))
        self._module_relay_event_prefixes = ('sock.', 'fwd.')
        self._module_relay_logger_prefixes = (
            'sfb.modules.socks',
            'sfb.modules.port_fwd',
            'SocksServerModule',
            'SocksRelayModule',
            'PortForwardServerModule',
            'PortForwardRelayModule',
        )
        self._module_file_transfer_enabled = bool(
            getattr(config, 'log_component_module_file_transfer', True)
        )
        self._module_file_transfer_logger_prefixes = (
            'sfb.modules.file_transfer',
            'FileTransferModule',
        )
        self._module_nc_linux_enabled = bool(
            getattr(config, 'log_component_module_nc_linux', True)
        )
        self._module_nc_linux_event_prefix = 'nc.'
        self._module_nc_linux_logger_prefixes = (
            'sfb.modules.nc_linux',
            'NcLinuxModule',
        )

    def filter(self, record):
        if record.levelno >= logging.ERROR:
            return True
        event = getattr(record, 'event', None)
        if event is not None:
            event_text = _coerce_text(event)
            if not self._dns_enabled and event_text.startswith(self._dns_event_prefixes):
                return False
            if not self._tls_enabled and event_text.startswith(self._tls_event_prefix):
                return False
            if not self._tunnel_enabled and event_text.startswith(self._tunnel_event_prefix):
                return False
            if not self._channel_enabled and event_text.startswith(self._channel_event_prefix):
                return False
            if (not self._module_relay_enabled and
                    event_text.startswith(self._module_relay_event_prefixes)):
                return False
            if not self._module_nc_linux_enabled and event_text.startswith(self._module_nc_linux_event_prefix):
                return False
            if self._event_whitelist and not _match_any(event_text, self._event_whitelist):
                return False
            if self._event_blacklist and _match_any(event_text, self._event_blacklist):
                return False
        name = getattr(record, 'name', '')
        if not self._dns_enabled and name.startswith(self._dns_logger_prefixes):
            return False
        if not self._icmp_enabled and name.startswith(self._icmp_logger_prefixes):
            return False
        if not self._tls_enabled and name.startswith(self._tls_logger_prefixes):
            return False
        if not self._tunnel_enabled and name.startswith(self._tunnel_logger_prefixes):
            return False
        if not self._channel_enabled and name.startswith(self._channel_logger_prefixes):
            return False
        if not self._protocol_enabled and name.startswith(self._protocol_logger_prefixes):
            return False
        if not self._module_relay_enabled and name.startswith(self._module_relay_logger_prefixes):
            return False
        if (not self._module_file_transfer_enabled and
                name.startswith(self._module_file_transfer_logger_prefixes)):
            return False
        if (not self._module_nc_linux_enabled and
                name.startswith(self._module_nc_linux_logger_prefixes)):
            return False
        return True


def add_component_filters(logger, config):
    """
    Attach component-based filters to all handlers on a logger.
    """
    filt = ComponentFilter(config)
    for handler in logger.handlers:
        if _handler_has_component_filter(handler):
            continue
        handler.addFilter(filt)
    return filt


class SQLiteLogHandler(logging.Handler):
    """
    Log handler that writes records to SQLite in a background thread.
    """

    _SENTINEL = object()

    def __init__(self, db_path, flush_interval=0.5, queue_maxsize=0):
        logging.Handler.__init__(self)
        self._db_path = db_path
        self._flush_interval = float(flush_interval)
        if queue_maxsize:
            self._queue = queue.Queue(maxsize=int(queue_maxsize))
        else:
            self._queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            name='sfb-sqlite-log',
        )
        self._thread.daemon = True
        self._thread.start()

    def emit(self, record):
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        event_name = _coerce_text(getattr(record, 'event', None))
        fields = _encode_fields(_resolve_fields(record))
        event = (
            float(record.created),
            _coerce_text(record.levelname),
            _coerce_text(record.name),
            _coerce_text(message),
            event_name,
            fields,
            _coerce_text(record.pathname),
            int(record.lineno),
            _coerce_text(record.funcName),
            _coerce_sqlite_int(getattr(record, 'thread', 0)),
            _coerce_sqlite_int(getattr(record, 'process', 0)),
        )
        try:
            self._queue.put_nowait(event)
        except Exception:
            # Drop record if queue is full or unavailable.
            pass

    def close(self):
        self._stop.set()
        try:
            self._queue.put_nowait(self._SENTINEL)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logging.Handler.close(self)

    def _worker(self):
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                'CREATE TABLE IF NOT EXISTS logs ('
                'id INTEGER PRIMARY KEY AUTOINCREMENT,'
                'created REAL,'
                'level TEXT,'
                'logger TEXT,'
                'message TEXT,'
                'event TEXT,'
                'fields TEXT,'
                'pathname TEXT,'
                'lineno INTEGER,'
                'func TEXT,'
                'thread INTEGER,'
                'process INTEGER'
                ')'
            )
            _ensure_log_columns(cur)
            _ensure_log_indexes(cur)
            conn.commit()

            batch = []
            last_flush = time_provider.now()
            flush_interval = self._flush_interval
            if flush_interval < 0:
                flush_interval = 0.0

            while not self._stop.is_set():
                if batch:
                    timeout = flush_interval - (time_provider.now() - last_flush)
                    if timeout < 0:
                        timeout = 0.0
                else:
                    timeout = flush_interval if flush_interval > 0 else None
                try:
                    if timeout is None:
                        item = self._queue.get()
                    else:
                        item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = None

                if item is self._SENTINEL:
                    self._stop.set()
                    item = None

                if item is not None:
                    batch.append(item)

                if batch and (len(batch) >= 100 or time_provider.now() - last_flush >= self._flush_interval):
                    cur.executemany(
                        'INSERT INTO logs (created, level, logger, message, event, fields, pathname, lineno, func, thread, process) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        batch,
                    )
                    conn.commit()
                    batch = []
                    last_flush = time_provider.now()

            if batch:
                cur.executemany(
                    'INSERT INTO logs (created, level, logger, message, event, fields, pathname, lineno, func, thread, process) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    batch,
                )
                conn.commit()
        finally:
            conn.close()


def get_logger(name):
    """
    Get a module logger under the sfb namespace.
    """
    if name.startswith(('sfb.', 'tunnel.')):
        return logging.getLogger(name)
    return logging.getLogger('sfb.' + name)


def _coerce_level(level):
    if isinstance(level, text_type):
        return getattr(logging, level.upper(), logging.INFO)
    return level


def _coerce_text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    try:
        return text_type(value)
    except Exception:
        return text_type(repr(value))


def _coerce_sqlite_int(value):
    if value is None:
        return 0
    try:
        value = int(value)
    except Exception:
        return 0
    if value < _SQLITE_INT64_MIN or value > _SQLITE_INT64_MAX:
        return _coerce_text(value)
    return value


def _resolve_fields(record):
    fields = getattr(record, 'fields', None)
    if callable(fields):
        fields = fields()
        setattr(record, 'fields', fields)
    return fields


def _encode_fields(fields):
    if fields is None:
        return None
    try:
        return json.dumps(fields, ensure_ascii=True, sort_keys=True)
    except Exception:
        return _coerce_text(fields)


def _normalize_patterns(patterns):
    if not patterns:
        return ()
    if isinstance(patterns, text_type):
        return (_coerce_text(patterns),)
    try:
        items = list(patterns)
    except Exception:
        return (_coerce_text(patterns),)
    normalized = []
    for item in items:
        if item is None:
            continue
        normalized.append(_coerce_text(item))
    return tuple(normalized)


def _match_any(value, patterns):
    for pattern in patterns:
        if fnmatch.fnmatch(value, pattern):
            return True
    return False


def _ensure_log_columns(cursor):
    cursor.execute('PRAGMA table_info(logs)')
    existing = set()
    for row in cursor.fetchall():
        existing.add(row[1])
    if 'event' not in existing:
        cursor.execute('ALTER TABLE logs ADD COLUMN event TEXT')
    if 'fields' not in existing:
        cursor.execute('ALTER TABLE logs ADD COLUMN fields TEXT')


def _ensure_log_indexes(cursor):
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_created ON logs (created)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs (logger)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_level ON logs (level)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_event ON logs (event)')


def _handler_has_component_filter(handler):
    for filt in handler.filters:
        if isinstance(filt, ComponentFilter):
            return True
    return False


def _ensure_handler(logger, handler_cls, formatter, *args):
    for handler in logger.handlers:
        if isinstance(handler, handler_cls):
            return
    handler = handler_cls(*args)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _truncate_lines(text, max_len):
    if max_len is None or max_len <= 0:
        return text
    lines = text.splitlines(True)
    if not lines:
        return text
    out = []
    for line in lines:
        body, ending = _split_line_ending(line)
        if len(body) > max_len:
            if max_len > 3:
                body = body[:max_len - 3] + '...'
            else:
                body = body[:max_len]
        out.append(body + ending)
    return ''.join(out)


def _split_line_ending(line):
    if line.endswith('\r\n'):
        return line[:-2], '\r\n'
    if line.endswith('\n'):
        return line[:-1], '\n'
    if line.endswith('\r'):
        return line[:-1], '\r'
    return line, ''
