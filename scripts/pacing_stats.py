# -*- coding: ascii -*-
"""
Summarize adaptive pacing metrics from a log database.
"""

from __future__ import absolute_import, print_function

import argparse
import json
import os
import sqlite3
import sys


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEFAULT_DB = os.path.join(ROOT_DIR, 'logs', 'client_log.db')
DEFAULT_EVENTS = (
    'tunnel.pacer_target',
    'tunnel.pacer_adjust',
    'tunnel.pacer_state',
)
METRICS = (
    'ack_rate_ewma',
    'feedback_target',
    'block_penalty',
)

try:
    text_type = unicode
except NameError:
    text_type = str
try:
    binary_type = bytes
except NameError:
    binary_type = str


class Stat(object):
    def __init__(self, name):
        self.name = name
        self.count = 0
        self.total = 0.0
        self.min_value = None
        self.max_value = None

    def add(self, value):
        if value is None:
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        self.count += 1
        self.total += number
        if self.min_value is None or number < self.min_value:
            self.min_value = number
        if self.max_value is None or number > self.max_value:
            self.max_value = number

    def avg(self):
        if self.count == 0:
            return None
        return self.total / float(self.count)


def _decode_text(data):
    if data is None:
        return None
    if isinstance(data, text_type):
        return data
    if isinstance(data, binary_type):
        encoding = 'mbcs' if os.name == 'nt' else 'utf-8'
        try:
            return data.decode(encoding)
        except Exception:
            return data.decode('utf-8', 'replace')
    return text_type(data)


def _split_events(value):
    if not value:
        return []
    items = []
    for item in value.split(','):
        item = item.strip()
        if item:
            items.append(item)
    return items


def _format_value(value):
    if value is None:
        return 'n/a'
    return '%.3f' % value


def _format_stat(stat, total_rows):
    missing = total_rows - stat.count if total_rows else 0
    return (
        'count=%d missing=%d min=%s avg=%s max=%s' % (
            stat.count,
            missing,
            _format_value(stat.min_value),
            _format_value(stat.avg()),
            _format_value(stat.max_value),
        )
    )


def main(argv):
    parser = argparse.ArgumentParser(
        description='Summarize adaptive pacing metrics from a log database.'
    )
    parser.add_argument(
        '--db',
        default=DEFAULT_DB,
        help='Path to SQLite log database (default: %s)' % DEFAULT_DB,
    )
    parser.add_argument(
        '--events',
        default=','.join(DEFAULT_EVENTS),
        help='Comma-separated pacer events to include (default: %s)' %
             ','.join(DEFAULT_EVENTS),
    )
    parser.add_argument(
        '--side',
        default='alice',
        help='Filter by fields["side"] (alice/bob). Use "any" for no filter.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Limit to latest rows (0 = all).',
    )
    args = parser.parse_args(argv)

    db_path = args.db
    if not os.path.isfile(db_path):
        print('Database not found: %s' % db_path, file=sys.stderr)
        return 1

    events = _split_events(args.events)
    if not events:
        print('No events specified.', file=sys.stderr)
        return 1

    side_filter = args.side
    if side_filter:
        side_filter = side_filter.strip().lower()
        if side_filter == 'any':
            side_filter = None

    stats = dict((name, Stat(name)) for name in METRICS)
    event_counts = dict((event, 0) for event in events)
    total_rows = 0
    parsed_rows = 0
    filtered_rows = 0
    empty_fields = 0
    parse_errors = 0

    placeholders = ','.join(['?'] * len(events))
    query = 'select event, fields from logs where event in (%s)' % placeholders
    params = list(events)
    if args.limit and args.limit > 0:
        query += ' order by created desc limit ?'
        params.append(int(args.limit))

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        for event, fields_raw in cursor.execute(query, params):
            total_rows += 1
            event_counts[event] = event_counts.get(event, 0) + 1
            text = _decode_text(fields_raw)
            if not text:
                empty_fields += 1
                continue
            try:
                fields = json.loads(text)
            except ValueError:
                parse_errors += 1
                continue
            if not isinstance(fields, dict):
                parse_errors += 1
                continue
            if side_filter:
                side_value = fields.get('side')
                side_value = _decode_text(side_value)
                if side_value is None:
                    side_value = ''
                if side_value.lower() != side_filter:
                    filtered_rows += 1
                    continue
            parsed_rows += 1
            for name in METRICS:
                stats[name].add(fields.get(name))
    finally:
        conn.close()

    print('DB: %s' % db_path)
    print('Events: %s' % ', '.join(events))
    print('Side: %s' % (side_filter if side_filter else 'any'))
    print('Rows: %d (parsed=%d, filtered=%d, empty=%d, errors=%d)' % (
        total_rows,
        parsed_rows,
        filtered_rows,
        empty_fields,
        parse_errors,
    ))
    print('Event counts:')
    for event in events:
        print('  %s: %d' % (event, event_counts.get(event, 0)))
    print('Metrics:')
    for name in METRICS:
        stat = stats[name]
        print('  %s: %s' % (name, _format_stat(stat, parsed_rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
