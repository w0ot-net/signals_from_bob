# -*- coding: ascii -*-
"""
cProfile helpers for multi-threaded profiling.
"""

from __future__ import absolute_import

import cProfile
import pstats
import threading


class CProfileManager(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._profiles = []
        self._original_run = None
        self._main_profiler = None
        self._enabled = False

    def start(self):
        if self._enabled:
            return
        self._enabled = True
        self._original_run = threading.Thread.run
        manager = self

        def run_with_profile(thread_self, *args, **kwargs):
            profiler = cProfile.Profile()
            manager._register_profiler(profiler)
            profiler.enable()
            try:
                return manager._original_run(thread_self, *args, **kwargs)
            finally:
                profiler.disable()

        threading.Thread.run = run_with_profile
        self._main_profiler = cProfile.Profile()
        self._register_profiler(self._main_profiler)
        self._main_profiler.enable()

    def stop(self):
        if not self._enabled:
            return
        if self._main_profiler is not None:
            self._main_profiler.disable()
        if self._original_run is not None:
            threading.Thread.run = self._original_run
        self._enabled = False
        self._original_run = None

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
