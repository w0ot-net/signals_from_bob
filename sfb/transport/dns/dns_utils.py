# -*- coding: ascii -*-
"""
DNS utility functions for resolver detection.
"""

import logging
import os
import socket

_LOG = logging.getLogger(__name__)


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
        handle = open('/etc/resolv.conf', 'r')
    except (IOError, OSError):
        return []
    with handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if parts[0] != 'nameserver' or len(parts) < 2:
                continue
            host = parts[1]
            for addr in _resolve_host(host, 53):
                if addr not in resolvers:
                    resolvers.append(addr)
    return resolvers


def _load_windows_resolvers():
    """Load and test resolvers on Windows systems."""
    candidates = []

    # Gather candidates from ipconfig /all (most reliable for VPNs etc)
    for ip in _parse_ipconfig_dns():
        addr = (ip, 53)
        if addr not in candidates:
            candidates.append(addr)

    # Also gather from registry as fallback
    try:
        try:
            import winreg
        except ImportError:
            import _winreg as winreg

        values = []
        base_path = r'SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters'
        values.extend(_read_registry_nameservers(winreg, base_path))
        interfaces = base_path + r'\\Interfaces'
        for subkey in _enum_registry_keys(winreg, interfaces):
            values.extend(_read_registry_nameservers(
                winreg, interfaces + r'\\' + subkey
            ))

        for host in _split_nameserver_values(values):
            for addr in _resolve_host(host, 53):
                if addr not in candidates:
                    candidates.append(addr)
    except ImportError:
        pass

    # Test each candidate and return the first one that works
    for addr in candidates:
        _LOG.debug('Testing resolver %s:%d', addr[0], addr[1])
        if test_resolver(addr):
            _LOG.debug('Resolver %s:%d works', addr[0], addr[1])
            return [addr]
        _LOG.debug('Resolver %s:%d failed', addr[0], addr[1])

    # No working resolver found, return all candidates (let caller handle)
    return candidates


def _read_registry_nameservers(winreg, path):
    """Read nameserver values from a Windows registry path."""
    values = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return values
    try:
        for name in ('NameServer', 'DhcpNameServer'):
            try:
                value, _ = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            if value:
                values.append(value)
    finally:
        winreg.CloseKey(key)
    return values


def _enum_registry_keys(winreg, path):
    """Enumerate subkeys under a Windows registry path."""
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return []
    names = []
    index = 0
    try:
        while True:
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                break
            names.append(name)
            index += 1
    finally:
        winreg.CloseKey(key)
    return names


def _split_nameserver_values(values):
    """Split nameserver registry values into individual hosts."""
    hosts = []
    for value in values:
        try:
            text = value.strip()
        except AttributeError:
            continue
        if not text:
            continue
        text = text.replace(',', ' ')
        for host in text.split():
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def _parse_ipconfig_dns():
    """Parse ipconfig /all output for DNS server addresses."""
    import subprocess
    import re
    servers = []
    try:
        result = subprocess.run(
            ['ipconfig', '/all'],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stdout
    except (subprocess.SubprocessError, OSError):
        return servers

    # Match "DNS Servers" lines and continuation lines (indented IPs)
    # Example:
    #    DNS Servers . . . . . . . . . . . : 10.0.0.243
    #                                        8.8.8.8
    in_dns_section = False
    ip_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
    for line in output.splitlines():
        if 'DNS Servers' in line:
            in_dns_section = True
            match = ip_pattern.search(line)
            if match:
                ip = match.group(1)
                if ip not in servers:
                    servers.append(ip)
        elif in_dns_section:
            # Continuation lines are heavily indented
            stripped = line.strip()
            if stripped and ip_pattern.match(stripped):
                if stripped not in servers:
                    servers.append(stripped)
            else:
                # End of DNS servers for this adapter
                in_dns_section = False
    return servers


def test_resolver(addr, timeout=2.0):
    """
    Test if a resolver works by querying google.com.

    Args:
        addr: (host, port) tuple for the resolver
        timeout: Query timeout in seconds

    Returns:
        True if the resolver responded, False otherwise
    """
    import struct
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # Build minimal DNS query for google.com A record
        dns_id = 0x1234
        flags = 0x0100  # standard query, recursion desired
        # Header: ID, FLAGS, QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
        header = struct.pack('>HHHHHH', dns_id, flags, 1, 0, 0, 0)
        # Question: google.com, type A, class IN
        qname = b'\x06google\x03com\x00'
        question = qname + struct.pack('>HH', 1, 1)  # type A, class IN
        query = header + question
        sock.sendto(query, addr)
        data, _ = sock.recvfrom(512)
        # Check we got a response with matching ID
        if len(data) >= 2:
            resp_id = struct.unpack('>H', data[:2])[0]
            return resp_id == dns_id
        return False
    except (socket.error, socket.timeout, OSError):
        return False
    finally:
        if sock:
            sock.close()


def _resolve_host(host, port):
    """Resolve a hostname to (ip, port) tuples."""
    addrs = []
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET,
                                   socket.SOCK_DGRAM)
    except socket.gaierror:
        return []
    for family, socktype, proto, canonname, addr in infos:
        if addr not in addrs:
            addrs.append(addr)
    return addrs
