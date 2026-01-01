# -*- coding: ascii -*-
"""
Run icmp_socks_diag with cProfile and store results under profile_results/.
"""

from __future__ import absolute_import, print_function

import os
import subprocess
import sys
import time


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT_DIR, 'profile_results')


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def main():
    _ensure_dir(RESULTS_DIR)
    timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
    profile_path = os.path.join(
        RESULTS_DIR,
        'icmp_socks_diag_%s.pstats' % timestamp
    )
    cmd = [
        'python3', '-m', 'cProfile', '-o', profile_path,
        os.path.join(ROOT_DIR, 'scripts', 'icmp_socks_diag.py'),
        '--clients', '2',
        '--icmp-target', '127.0.0.1',
        '--icmp-mtu', '1400',
        '--send-rate', '0',
        '--log-profile', 'socks_throughput_debug',
        '--socks_relay_buffer_size', '32768',
        '--channel_max_send_buf', '262144',
        '--socks-pump-backoff-max', '0.0001',
        '--non-blocking-poll-timeout', '0',
        '--timeout', '900',
    ]
    sys.stdout.write('Writing profile to: %s\n' % profile_path)
    return subprocess.call(cmd, cwd=ROOT_DIR)


if __name__ == '__main__':
    sys.exit(main())
