# -*- coding: ascii -*-
"""
Logging helpers for the tunnel codebase.
"""

from __future__ import absolute_import

import logging
import sys
import threading
import time
import sqlite3
import json

try:
    import Queue as queue
except ImportError:
    import queue

from .compat import text_type


DEFAULT_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'


def configure_logging(level='INFO', to_stdout=True, log_file=None):
    """
    Configure tunnel logging.
    """
    logger = logging.getLogger('tunnel')
    logger.setLevel(_coerce_level(level))
    formatter = logging.Formatter(DEFAULT_FORMAT)

    if to_stdout:
        _ensure_handler(logger, logging.StreamHandler, formatter, sys.stdout)

    if log_file:
        _ensure_handler(logger, logging.FileHandler, formatter, log_file)


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
        fields = _encode_fields(getattr(record, 'fields', None))
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
            int(getattr(record, 'thread', 0) or 0),
            int(getattr(record, 'process', 0) or 0),
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
            last_flush = time.time()

            while not self._stop.is_set():
                timeout = max(0.0, self._flush_interval - (time.time() - last_flush))
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = None

                if item is self._SENTINEL:
                    self._stop.set()
                    item = None

                if item is not None:
                    batch.append(item)

                if batch and (len(batch) >= 100 or time.time() - last_flush >= self._flush_interval):
                    cur.executemany(
                        'INSERT INTO logs (created, level, logger, message, event, fields, pathname, lineno, func, thread, process) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        batch,
                    )
                    conn.commit()
                    batch = []
                    last_flush = time.time()

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
    Get a module logger under the tunnel namespace.
    """
    if name.startswith('tunnel.'):
        return logging.getLogger(name)
    return logging.getLogger('tunnel.' + name)


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


def _encode_fields(fields):
    if fields is None:
        return None
    try:
        return json.dumps(fields, ensure_ascii=True, sort_keys=True)
    except Exception:
        return _coerce_text(fields)


def _ensure_log_columns(cursor):
    cursor.execute('PRAGMA table_info(logs)')
    existing = set([row[1] for row in cursor.fetchall()])
    if 'event' not in existing:
        cursor.execute('ALTER TABLE logs ADD COLUMN event TEXT')
    if 'fields' not in existing:
        cursor.execute('ALTER TABLE logs ADD COLUMN fields TEXT')


def _ensure_log_indexes(cursor):
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_created ON logs (created)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs (logger)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_level ON logs (level)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_event ON logs (event)')


def _ensure_handler(logger, handler_cls, formatter, *args):
    for handler in logger.handlers:
        if isinstance(handler, handler_cls):
            return
    handler = handler_cls(*args)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
