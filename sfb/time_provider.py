# -*- coding: ascii -*-
"""
Monotonic time provider for tunnel timing.

Use now() for timeouts/durations and wall_time() for epoch timestamps.
"""

from __future__ import absolute_import

import sys
import threading
import time
import warnings


# Direct aliases avoid wrapper overhead.
sleep = time.sleep
wall_time = time.time


def _select_time_source():
    if hasattr(time, 'monotonic'):
        return time.monotonic, False
    if sys.platform.startswith('win') and hasattr(time, 'clock'):
        return time.clock, False
    return time.time, True


_DEFAULT_TIME_SOURCE, _DEFAULT_CLAMP = _select_time_source()
_time_source = _DEFAULT_TIME_SOURCE
_use_clamp = _DEFAULT_CLAMP
_clamp_lock = threading.Lock()
_clamp_last = None
_active_tunnel_lock = threading.Lock()
_active_tunnel_count = 0


def _should_warn_unclamped():
    if _use_clamp:
        return False
    if _time_source is _DEFAULT_TIME_SOURCE:
        return _DEFAULT_CLAMP
    return True


def _warn_unclamped_active():
    warnings.warn(
        'Unclamped time source in use while tunnels are active',
        RuntimeWarning,
    )


def register_tunnel():
    global _active_tunnel_count
    with _active_tunnel_lock:
        _active_tunnel_count += 1
        active = _active_tunnel_count
    if active == 1 and _should_warn_unclamped():
        _warn_unclamped_active()


def unregister_tunnel():
    global _active_tunnel_count
    with _active_tunnel_lock:
        if _active_tunnel_count > 0:
            _active_tunnel_count -= 1


def now():
    """
    Return a monotonic timestamp in seconds.
    """
    value = _time_source()
    if _use_clamp:
        global _clamp_last
        with _clamp_lock:
            if _clamp_last is None or value >= _clamp_last:
                _clamp_last = value
            else:
                value = _clamp_last
    return value


def set_time_source(source, clamp=None):
    """
    Override the time source for tests.
    """
    if not callable(source):
        raise TypeError('source must be callable')
    global _time_source, _use_clamp, _clamp_last
    _time_source = source
    if clamp is None:
        _use_clamp = _DEFAULT_CLAMP
    else:
        _use_clamp = bool(clamp)
    with _clamp_lock:
        _clamp_last = None
    active = False
    with _active_tunnel_lock:
        active = _active_tunnel_count > 0
    if active and _should_warn_unclamped():
        _warn_unclamped_active()


def reset_time_source():
    """
    Restore the default time source.
    """
    global _time_source, _use_clamp, _clamp_last
    _time_source = _DEFAULT_TIME_SOURCE
    _use_clamp = _DEFAULT_CLAMP
    with _clamp_lock:
        _clamp_last = None


def _get_state():
    return (_time_source, _use_clamp)


def _get_default_state():
    return (_DEFAULT_TIME_SOURCE, _DEFAULT_CLAMP)
