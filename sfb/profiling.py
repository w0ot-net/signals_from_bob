# -*- coding: ascii -*-
"""
Profiling helpers for multi-threaded profiling.

Uses the standard library profile module with a resync wrapper to tolerate
profiling mid-stack across threads.
"""

from __future__ import absolute_import

import pstats
import profile
import sys
import threading
import time


class CProfileManager(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._profiles = []
        self._original_run = None
        self._main_profiler = None
        self._main_prev_profile = None
        self._enabled = False
        self._timer = _select_timer()

    def start(self):
        if self._enabled:
            return
        self._enabled = True
        self._original_run = threading.Thread.run
        manager = self

        def run_with_profile(thread_self, *args, **kwargs):
            prev_profile = sys.getprofile()
            profiler = ResyncProfile(timer=manager._timer)
            profiler._reset_stack(sys._getframe())
            manager._register_profiler(profiler)
            sys.setprofile(profiler.dispatcher)
            try:
                return manager._original_run(thread_self, *args, **kwargs)
            finally:
                sys.setprofile(prev_profile)

        threading.Thread.run = run_with_profile
        self._main_prev_profile = sys.getprofile()
        self._main_profiler = ResyncProfile(timer=self._timer)
        self._main_profiler._reset_stack(sys._getframe())
        self._register_profiler(self._main_profiler)
        sys.setprofile(self._main_profiler.dispatcher)

    def stop(self):
        if not self._enabled:
            return
        if self._main_prev_profile is not None or sys.getprofile() is not None:
            sys.setprofile(self._main_prev_profile)
        if self._original_run is not None:
            threading.Thread.run = self._original_run
        self._enabled = False
        self._original_run = None
        self._main_prev_profile = None

    def dump_stats(self, path):
        stats = self._merge_stats()
        stats.dump_stats(path)

    def _register_profiler(self, profiler):
        with self._lock:
            self._profiles.append(profiler)

    def _merge_stats(self):
        with self._lock:
            profiles = list(self._profiles)
        if not profiles:
            raise ValueError('no profiles captured')
        stats = pstats.Stats(profiles[0])
        for profiler in profiles[1:]:
            stats.add(profiler)
        return stats


def _select_timer():
    if hasattr(time, 'thread_time'):
        return time.thread_time
    if hasattr(time, 'process_time'):
        return time.process_time
    if hasattr(time, 'clock'):
        return time.clock
    return time.time


class ResyncProfile(profile.Profile):
    def _reset_stack(self, frame):
        stack = []
        while frame is not None:
            stack.append(frame)
            frame = frame.f_back
        cur = None
        timings = self.timings
        for frame in reversed(stack):
            code = frame.f_code
            fn = (code.co_filename, code.co_firstlineno, code.co_name)
            if fn not in timings:
                timings[fn] = (0, 0, 0.0, 0.0, {})
            cur = (0.0, 0.0, 0.0, fn, frame, cur)
        self.cur = cur
        self.t = self.get_time()

    def trace_dispatch_call(self, frame, t):
        if self.cur and frame.f_back is not self.cur[-2]:
            self._reset_stack(frame.f_back)
        return profile.Profile.trace_dispatch_call(self, frame, t)

    def trace_dispatch_return(self, frame, t):
        if self.cur and frame is not self.cur[-2]:
            self._reset_stack(frame)
        return profile.Profile.trace_dispatch_return(self, frame, t)

    dispatch = {
        'call': trace_dispatch_call,
        'exception': profile.Profile.trace_dispatch_exception,
        'return': trace_dispatch_return,
        'c_call': profile.Profile.trace_dispatch_c_call,
        'c_exception': trace_dispatch_return,
        'c_return': trace_dispatch_return,
    }
