# -*- coding: ascii -*-
"""
ICMP + SOCKS scp upload harness with proxychains config.

Flow:
- Launch Bob with ICMP transport and the socks module.
- Launch Alice with ICMP transport targeting 127.0.0.1 by default.
- Write a custom proxychains config and use it (no /etc config).
- Use proxychains scp to upload the 2MB test file to /tmp/del.
- Write SQLite logs to logs/icmp_scp_server_log.db and logs/icmp_scp_client_log.db.

Requirements:
- Linux with root privileges (ICMP transport uses raw sockets).
- Kernel echo replies must be disabled:
    sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1
"""

from __future__ import absolute_import, print_function

import argparse
import getpass
import os
import platform
import socket
import subprocess
import sys

try:
    from shlex import quote as shell_quote
except ImportError:
    from pipes import quote as shell_quote


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sfb import time_provider


LOG_DIR = os.path.join(ROOT_DIR, 'logs')
SERVER_DB_LOG = os.path.join(LOG_DIR, 'icmp_scp_server_log.db')
CLIENT_DB_LOG = os.path.join(LOG_DIR, 'icmp_scp_client_log.db')
DEFAULT_PROXYCHAINS_CONFIG = '/tmp/proxychains.conf'
DEFAULT_PROXYCHAINS_CONNECT_TIMEOUT_MS = 15000
DEFAULT_PROXYCHAINS_READ_TIMEOUT_MS = 600000
DEFAULT_SOCKS_PORT = 1080
DEFAULT_REMOTE_FILE = '/tmp/del'
DEFAULT_TEST_FILE = os.path.join('test_download_files', '100MB.bin')


class ManagedProcess(object):
    """Thin wrapper to start/stop subprocesses with timeouts."""

    def __init__(self, name, cmd, cwd=None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd)

    def stop(self, timeout=5.0):
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
        except Exception:
            return
        start = time_provider.now()
        while time_provider.now() - start < timeout:
            if self.proc.poll() is not None:
                return
            time_provider.sleep(0.1)
        try:
            self.proc.kill()
        except Exception:
            pass

    def poll(self):
        if self.proc is None:
            return None
        return self.proc.poll()


def parse_args():
    parser = argparse.ArgumentParser(
        description='ICMP SOCKS scp upload harness'
    )
    parser.add_argument(
        '--proxychains-bin', default='proxychains',
        help='proxychains binary (default: proxychains)'
    )
    parser.add_argument(
        '--proxychains-config', default=None,
        help='Write proxychains config to PATH '
             '(default: /tmp/proxychains.conf)'
    )
    parser.add_argument(
        '--proxychains-chain',
        choices=('strict_chain', 'dynamic_chain', 'random_chain'),
        default='strict_chain',
        help='Proxychains chain mode (default: strict_chain)'
    )
    parser.add_argument(
        '--proxychains-connect-timeout', type=int,
        default=DEFAULT_PROXYCHAINS_CONNECT_TIMEOUT_MS,
        help='Proxychains tcp_connect_time_out in ms (default: %d)' %
             DEFAULT_PROXYCHAINS_CONNECT_TIMEOUT_MS
    )
    parser.add_argument(
        '--proxychains-read-timeout', type=int,
        default=DEFAULT_PROXYCHAINS_READ_TIMEOUT_MS,
        help='Proxychains tcp_read_time_out in ms (default: %d)' %
             DEFAULT_PROXYCHAINS_READ_TIMEOUT_MS
    )
    parser.add_argument(
        '--proxychains-proxy-dns', action='store_true',
        help='Enable proxy_dns in proxychains config'
    )
    parser.add_argument(
        '--proxychains-quiet', action='store_true',
        help='Enable quiet_mode in proxychains config'
    )
    parser.add_argument(
        '--socks-host', default='127.0.0.1',
        help='SOCKS server host (default: 127.0.0.1)'
    )
    parser.add_argument(
        '--socks-port', type=int, default=DEFAULT_SOCKS_PORT,
        help='SOCKS server port (default: %d)' % DEFAULT_SOCKS_PORT
    )
    parser.add_argument(
        '--target', default='127.0.0.1',
        help='ICMP target for Alice to reach Bob (default: 127.0.0.1)'
    )
    parser.add_argument(
        '--icmp-mtu', type=int, default=None,
        help='Override ICMP payload MTU (passed to sfb CLI)'
    )
    parser.add_argument(
        '--max-in-flight',
        dest='max_in_flight', type=int, default=None,
        help='Override max-in-flight (passed to sfb CLI)'
    )
    parser.add_argument(
        '--log-profile', default='scp_stalled_icmp_socks',
        help='Log profile for Bob/Alice (default: scp_stalled_icmp_socks)'
    )
    parser.add_argument(
        '--db-log-flush', type=float, default=2.0,
        help='SQLite log flush interval for bob/alice (default: 2.0)'
    )
    parser.add_argument(
        '--verbose-cli', action='store_true',
        help='Pass -v to sfb CLI for debug-level logs'
    )
    parser.add_argument(
        '--timeout', type=float, default=300.0,
        help='Max seconds to wait for setup and scp (default: 300)'
    )
    parser.add_argument(
        '--scp-bin', default='scp',
        help='scp binary (default: scp)'
    )
    parser.add_argument(
        '--ssh-bin', default='ssh',
        help='ssh binary (default: ssh)'
    )
    parser.add_argument(
        '--ssh-user', default=None,
        help='SSH username (default: current user)'
    )
    parser.add_argument(
        '--ssh-host', default='127.0.0.1',
        help='SSH host (default: 127.0.0.1)'
    )
    parser.add_argument(
        '--ssh-port', type=int, default=22,
        help='SSH port (default: 22)'
    )
    parser.add_argument(
        '--identity-file', default=None,
        help='SSH identity file for scp/ssh'
    )
    parser.add_argument(
        '--ssh-option', action='append', default=[],
        help='Additional ssh -o options (repeatable)'
    )
    parser.add_argument(
        '--scp-verbose', action='store_true',
        help='Enable scp -v'
    )
    parser.add_argument(
        '--scp-quiet', action='store_true',
        help='Enable scp -q'
    )
    parser.add_argument(
        '--local-file', default=DEFAULT_TEST_FILE,
        help='Local file to upload (default: %s)' % DEFAULT_TEST_FILE
    )
    parser.add_argument(
        '--remote-file', default=DEFAULT_REMOTE_FILE,
        help='Remote file path for upload (default: %s)' % DEFAULT_REMOTE_FILE
    )
    return parser.parse_args()


def has_explicit_log_profile(argv):
    for arg in argv[1:]:
        if arg == '--log-profile' or arg.startswith('--log-profile='):
            return True
    return False


def require_linux_root():
    if platform.system().lower() != 'linux':
        raise SystemExit('ICMP transport is only supported on Linux')
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('ICMP transport requires root privileges (run as root)')
    path = '/proc/sys/net/ipv4/icmp_echo_ignore_all'
    try:
        with open(path, 'r') as handle:
            value = handle.read().strip()
    except (IOError, OSError):
        raise SystemExit('Unable to read %s to verify kernel ICMP settings' % path)
    if value != '1':
        raise SystemExit(
            'Kernel ICMP echo replies are enabled (value=%s). '
            'Disable with: sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1' % value
        )


def ensure_logs():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    for path in (SERVER_DB_LOG, CLIENT_DB_LOG):
        try:
            os.remove(path)
        except OSError:
            pass


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def wait_for_port(host, port, deadline, proc=None):
    """Wait until a TCP port is accepting connections."""
    while time_provider.now() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError('%s exited with code %s' % (proc.name, proc.poll()))
        try:
            sock = socket.create_connection((host, port), timeout=1.0)
            sock.close()
            return True
        except socket.error:
            time_provider.sleep(0.2)
    return False


def find_executable(name):
    if os.path.isabs(name) and os.path.isfile(name) and os.access(name, os.X_OK):
        return name
    paths = os.environ.get('PATH', '').split(os.pathsep)
    exts = ['']
    if os.name == 'nt':
        exts = os.environ.get('PATHEXT', '').split(os.pathsep)
        exts = [ext for ext in exts if ext]
        if not exts:
            exts = ['.exe', '.bat', '.cmd']
    for path in paths:
        if not path:
            continue
        candidate = os.path.join(path, name)
        for ext in exts:
            final = candidate + ext
            if os.path.isfile(final) and os.access(final, os.X_OK):
                return final
    return None


def resolve_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(ROOT_DIR, path)


def write_proxychains_config(path, socks_host, socks_port, chain,
                             read_timeout_ms, connect_timeout_ms,
                             proxy_dns, quiet):
    if os.path.isdir(path):
        raise SystemExit('proxychains config path is a directory: %s' % path)
    ensure_parent_dir(path)
    lines = [
        '# Autogenerated by icmp_socks_scp_test.py',
        chain,
    ]
    if proxy_dns:
        lines.append('proxy_dns')
    if quiet:
        lines.append('quiet_mode')
    lines.extend([
        'tcp_read_time_out %d' % read_timeout_ms,
        'tcp_connect_time_out %d' % connect_timeout_ms,
        '',
        '[ProxyList]',
        'socks5 %s %d' % (socks_host, socks_port),
        '',
    ])
    with open(path, 'w') as handle:
        handle.write('\n'.join(lines))


def build_proxychains_prefix(proxychains_bin, config_path):
    return [proxychains_bin, '-f', config_path]


def build_ssh_base(cmd_bin, ssh_port, identity_file, ssh_options):
    cmd = [cmd_bin]
    if ssh_port:
        cmd.extend(['-p', str(ssh_port)])
    if identity_file:
        cmd.extend(['-i', identity_file])
    for opt in ssh_options:
        cmd.extend(['-o', opt])
    return cmd


def start_bob(socks_host, socks_port, icmp_mtu=None, max_in_flight=None,
              log_profile=None, verbose=False, db_log_flush=None):
    listen_addr = '%s:%d' % (socks_host, socks_port)
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'bob',
        '--transport', 'icmp',
    ]
    if verbose:
        cmd.append('-v')
    cmd.extend(['--db-log', SERVER_DB_LOG])
    cmd.extend(['--log-profile', log_profile or 'scp_stalled_icmp_socks'])
    if db_log_flush is not None:
        cmd.extend(['--db-log-flush', str(db_log_flush)])
    if icmp_mtu:
        cmd.extend(['--icmp-mtu', str(icmp_mtu)])
    if max_in_flight is not None:
        cmd.extend(['--max-in-flight', str(max_in_flight)])
    cmd.extend([
        '--module', 'socks',
        '--socks-listen', listen_addr,
    ])
    return ManagedProcess('bob', cmd, cwd=ROOT_DIR)


def start_alice(target, icmp_mtu=None, max_in_flight=None,
                log_profile=None, verbose=False, db_log_flush=None):
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'alice',
        '--transport', 'icmp',
        '--target', target,
    ]
    if verbose:
        cmd.append('-v')
    cmd.extend(['--db-log', CLIENT_DB_LOG])
    cmd.extend(['--log-profile', log_profile or 'scp_stalled_icmp_socks'])
    if db_log_flush is not None:
        cmd.extend(['--db-log-flush', str(db_log_flush)])
    if icmp_mtu:
        cmd.extend(['--icmp-mtu', str(icmp_mtu)])
    if max_in_flight is not None:
        cmd.extend(['--max-in-flight', str(max_in_flight)])
    return ManagedProcess('alice', cmd, cwd=ROOT_DIR)


def run_command(cmd, timeout):
    proc = subprocess.Popen(cmd)
    deadline = time_provider.now() + timeout if timeout else None
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        if deadline and time_provider.now() >= deadline:
            try:
                proc.terminate()
            except Exception:
                pass
            return 1
        time_provider.sleep(0.2)


def main():
    args = parse_args()
    if args.scp_verbose and args.scp_quiet:
        raise SystemExit('Only one of --scp-verbose or --scp-quiet can be set')
    if has_explicit_log_profile(sys.argv):
        args.verbose_cli = True

    if args.proxychains_connect_timeout <= 0:
        raise SystemExit('--proxychains-connect-timeout must be > 0')
    if args.proxychains_read_timeout <= 0:
        raise SystemExit('--proxychains-read-timeout must be > 0')

    require_linux_root()
    ensure_logs()

    proxychains_bin = find_executable(args.proxychains_bin)
    if not proxychains_bin and args.proxychains_bin == 'proxychains':
        proxychains_bin = find_executable('proxychains4')
    if not proxychains_bin:
        raise SystemExit('proxychains not found: %s' % args.proxychains_bin)

    scp_bin = find_executable(args.scp_bin)
    if not scp_bin:
        raise SystemExit('scp not found: %s' % args.scp_bin)
    ssh_bin = find_executable(args.ssh_bin)
    if not ssh_bin:
        raise SystemExit('ssh not found: %s' % args.ssh_bin)

    local_file = resolve_path(args.local_file)
    if not os.path.isfile(local_file):
        raise SystemExit('Local file not found: %s' % local_file)

    proxychains_config = args.proxychains_config
    if proxychains_config:
        proxychains_config = resolve_path(proxychains_config)
    else:
        proxychains_config = DEFAULT_PROXYCHAINS_CONFIG

    write_proxychains_config(
        proxychains_config,
        args.socks_host,
        args.socks_port,
        args.proxychains_chain,
        args.proxychains_read_timeout,
        args.proxychains_connect_timeout,
        args.proxychains_proxy_dns,
        args.proxychains_quiet,
    )

    sys.stdout.write('Proxychains config: %s\n' % proxychains_config)

    ssh_user = args.ssh_user or getpass.getuser()
    remote_file = args.remote_file
    if not remote_file:
        raise SystemExit('Remote file must be non-empty')

    proxychains_prefix = build_proxychains_prefix(
        proxychains_bin,
        proxychains_config,
    )
    ssh_base = build_ssh_base(
        ssh_bin,
        args.ssh_port,
        args.identity_file,
        args.ssh_option,
    )
    scp_cmd = [scp_bin]
    if args.scp_verbose:
        scp_cmd.append('-v')
    if args.scp_quiet:
        scp_cmd.append('-q')
    if args.ssh_port:
        scp_cmd.extend(['-P', str(args.ssh_port)])
    if args.identity_file:
        scp_cmd.extend(['-i', args.identity_file])
    for opt in args.ssh_option:
        scp_cmd.extend(['-o', opt])
    scp_cmd.extend([
        local_file,
        '%s@%s:%s' % (ssh_user, args.ssh_host, remote_file),
    ])

    bob = None
    alice = None
    try:
        bob = start_bob(
            args.socks_host,
            args.socks_port,
            icmp_mtu=args.icmp_mtu,
            max_in_flight=args.max_in_flight,
            log_profile=args.log_profile,
            verbose=args.verbose_cli,
            db_log_flush=args.db_log_flush,
        )
        alice = start_alice(
            args.target,
            icmp_mtu=args.icmp_mtu,
            max_in_flight=args.max_in_flight,
            log_profile=args.log_profile,
            verbose=args.verbose_cli,
            db_log_flush=args.db_log_flush,
        )
        bob.start()
        time_provider.sleep(0.2)
        alice.start()

        deadline = time_provider.now() + args.timeout
        socks_ready = wait_for_port(
            args.socks_host,
            args.socks_port,
            deadline,
            proc=bob,
        )
        if not socks_ready:
            raise SystemExit('SOCKS server did not become ready before timeout')

        full_scp_cmd = proxychains_prefix + scp_cmd
        sys.stdout.write('Running: %s\n' % ' '.join(shell_quote(x) for x in full_scp_cmd))
        rc = run_command(full_scp_cmd, args.timeout)
        if rc != 0:
            raise SystemExit('scp failed with code %s' % rc)

        sys.stdout.write('Upload complete: %s -> %s\n' % (
            local_file,
            remote_file,
        ))
        sys.stdout.write('Logs: server=%s client=%s\n' % (
            SERVER_DB_LOG,
            CLIENT_DB_LOG,
        ))
        return 0
    finally:
        shutdown(bob, alice)


def shutdown(bob, alice):
    for proc in (alice, bob):
        try:
            if proc:
                proc.stop()
        except Exception:
            pass


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        sys.exit(1)
