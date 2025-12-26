# -*- coding: ascii -*-
"""
File transfer control message helpers.
"""

from __future__ import absolute_import

from ...control_message import ControlMessage, encode, validate

T_FILE = 'file'


def file_list(rid, path):
    return ControlMessage(T_FILE, 'list', rid=rid, path=path)


def file_list_ok(rid, files):
    return ControlMessage(T_FILE, 'list_ok', rid=rid, files=files)


def file_get(rid, ch, path):
    return ControlMessage(T_FILE, 'get', rid=rid, ch=ch, path=path)


def file_get_ok(rid, ch, size):
    return ControlMessage(T_FILE, 'get_ok', rid=rid, ch=ch, size=size)


def file_put(rid, ch, path, size):
    return ControlMessage(T_FILE, 'put', rid=rid, ch=ch, path=path, size=size)


def file_put_ok(rid, ch):
    return ControlMessage(T_FILE, 'put_ok', rid=rid, ch=ch)


def file_err(rid, code, reason, ch=None):
    fields = {'rid': rid, 'code': code, 'reason': reason}
    if ch is not None:
        fields['ch'] = ch
    return ControlMessage(T_FILE, 'err', **fields)
