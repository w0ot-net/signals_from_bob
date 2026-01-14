# -*- coding: ascii -*-
"""
DNS utility functions for resolver detection.
"""

import logging
import os
import re
import subprocess

from ...compat import to_native_str
from ...logging_util import get_logger, log_event

_LOG = get_logger(__name__)


def load_system_resolvers():
    """
    Load system DNS resolvers.

    Returns:
        List of (host, port) tuples for available resolvers.
    """
    if os.name == 'nt':
        return _load_windows_resolvers()
    return _load_unix_resolvers()


def _load_unix_resolvers():
    """Load resolvers from /etc/resolv.conf on Unix systems."""
    resolvers = []
    try:
        with open('/etc/resolv.conf', 'r') as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '#' in line:
                    line = line.split('#', 1)[0].strip()
                    if not line:
                        continue
                parts = line.split()
                if not parts:
                    continue
                if parts[0].lower() != 'nameserver' or len(parts) < 2:
                    continue
                ip = parts[1]
                addr = (ip, 53)
                if addr not in resolvers:
                    resolvers.append(addr)
    except (IOError, OSError):
        pass
    return resolvers


class _SimpleResult(object):
    def __init__(self, stdout):
        self.stdout = stdout


def _subprocess_error_types():
    errors = [OSError]
    subproc_error = getattr(subprocess, 'SubprocessError', None)
    if subproc_error is not None:
        errors.append(subproc_error)
    timeout_error = getattr(subprocess, 'TimeoutExpired', None)
    if timeout_error is not None:
        errors.append(timeout_error)
    return tuple(errors)


def _run_nslookup():
    args = ['nslookup', 'google.com']
    runner = getattr(subprocess, 'run', None)
    if runner is not None:
        return runner(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            universal_newlines=True,
        )
    return _run_nslookup_with_popen(args)


def _run_nslookup_with_popen(args):
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    timeout_error = getattr(subprocess, 'TimeoutExpired', None)
    try:
        try:
            output, _ = proc.communicate(timeout=5)
        except TypeError:
            output, _ = proc.communicate()
    except Exception as e:
        if timeout_error is not None and isinstance(e, timeout_error):
            try:
                if hasattr(proc, 'kill'):
                    proc.kill()
                else:
                    proc.terminate()
            except Exception:
                pass
            try:
                proc.communicate()
            except Exception:
                pass
        raise
    return _SimpleResult(output)


def _coerce_output(value):
    if value is None:
        return ''
    return to_native_str(value)


def _parse_nslookup_output(output):
    ip_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
    lines = output.splitlines()
    server_index = None
    for i, line in enumerate(lines):
        if line.startswith('Server:'):
            server_index = i
            break
    if server_index is None:
        return None
    for line in lines[server_index + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('Non-authoritative answer:'):
            break
        match = ip_pattern.search(stripped)
        if match:
            return match.group(1)
    return None


def _load_windows_resolvers():
    """Get the system resolver on Windows by parsing nslookup output."""
    try:
        result = _run_nslookup()
        output = _coerce_output(getattr(result, 'stdout', ''))
    except _subprocess_error_types() as e:
        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.resolver_lookup_failed',
                'nslookup failed',
                lambda: {'error': str(e)},
            )
        return []

    # Parse output like:
    # Server:  UnKnown
    # Address:  10.0.0.243
    #
    # Non-authoritative answer:
    # ...
    ip = _parse_nslookup_output(output)
    if ip:
        if _LOG.isEnabledFor(logging.DEBUG):
            log_event(
                _LOG,
                logging.DEBUG,
                'dns.resolver_found',
                'Found system resolver',
                lambda: {'ip': ip},
            )
        return [(ip, 53)]

    if _LOG.isEnabledFor(logging.DEBUG):
        log_event(
            _LOG,
            logging.DEBUG,
            'dns.resolver_parse_failed',
            'Could not parse resolver from nslookup output',
            lambda: None,
        )
    return []
