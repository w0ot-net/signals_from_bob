# -*- coding: ascii -*-
"""
SOCKS logging helpers.
"""

from __future__ import absolute_import

from ... import time_provider


def add_field(fields, key, value):
    if value is not None:
        fields[key] = value


def add_fields(fields, updates):
    if not updates:
        return fields
    for key, value in updates.items():
        if value is not None:
            fields[key] = value
    return fields


def normalize_peer(label):
    if label is None:
        return None
    return label.lower()


def sock_fields(rid=None, ch=None, side=None, peer=None, direction=None, label=None):
    fields = {}
    add_field(fields, 'rid', rid)
    add_field(fields, 'ch', ch)
    add_field(fields, 'side', side)
    add_field(fields, 'peer', peer)
    add_field(fields, 'direction', direction)
    add_field(fields, 'label', label)
    return fields


def duration_secs(start, end=None):
    if start is None:
        return None
    if end is None:
        end = time_provider.now()
    value = end - start
    if value < 0:
        value = 0.0
    return round(value, 3)
