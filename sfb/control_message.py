# -*- coding: ascii -*-
"""
Shared control message helpers.

Defines the ControlMessage base class plus validation and encoding helpers.
"""

from __future__ import absolute_import

import json


class ControlMessage(object):
    """Base control message container."""

    __slots__ = ('_type', '_command', '_fields')

    def __init__(self, msg_type, command, **fields):
        if not msg_type or not command:
            raise ValueError('ControlMessage requires t and c')
        self._type = msg_type
        self._command = command
        self._fields = fields

    @property
    def msg_type(self):
        return self._type

    @property
    def command(self):
        return self._command

    def to_dict(self):
        data = {'t': self._type, 'c': self._command}
        if self._fields:
            data.update(self._fields)
        return data


def validate(msg):
    """Validate a control message dict."""
    if not isinstance(msg, dict):
        raise ValueError('Control message must be a dict')
    msg_type = msg.get('t')
    cmd = msg.get('c')
    if not msg_type or not cmd:
        raise ValueError('Control message requires t and c')
    return msg


def encode(msg, validate_fields=True):
    """
    Encode a control message to bytes for transmission.

    Args:
        msg: ControlMessage or dict
        validate_fields: True to enforce required fields
    """
    if isinstance(msg, ControlMessage):
        msg = msg.to_dict()
    if validate_fields:
        validate(msg)
    line = json.dumps(msg, separators=(',', ':'), ensure_ascii=True)
    return line.encode('ascii') + b'\n'
