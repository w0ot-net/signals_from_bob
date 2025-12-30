# -*- coding: ascii -*-
"""
DNS utility functions for resolver detection.
"""

import os
import re
import subprocess

from ...logging_util import get_logger

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
                parts = line.split()
                if parts[0] != 'nameserver' or len(parts) < 2:
                    continue
                ip = parts[1]
                addr = (ip, 53)
                if addr not in resolvers:
                    resolvers.append(addr)
    except (IOError, OSError):
        pass
    return resolvers


def _load_windows_resolvers():
    """Get the system resolver on Windows by parsing nslookup output."""
    try:
        result = subprocess.run(
            ['nslookup', 'google.com'],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stdout
    except (subprocess.SubprocessError, OSError) as e:
        _LOG.debug('nslookup failed: %s', e)
        return []

    # Parse output like:
    # Server:  UnKnown
    # Address:  10.0.0.243
    #
    # Non-authoritative answer:
    # ...
    ip_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('Server:'):
            # The Address line follows the Server line
            if i + 1 < len(lines):
                addr_line = lines[i + 1]
                if 'Address:' in addr_line:
                    match = ip_pattern.search(addr_line)
                    if match:
                        ip = match.group(1)
                        _LOG.debug('Found system resolver: %s', ip)
                        return [(ip, 53)]
            break

    _LOG.debug('Could not parse resolver from nslookup output')
    return []
