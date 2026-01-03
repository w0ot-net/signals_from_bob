# -*- coding: ascii -*-
"""
Run a remote ICMP Bob and local ICMP Alice, plus proxychains tests, via SSH.

Flow:
- SSH to the remote host via ProxyJump and start Bob (git pull + sfb.cli).
- Start Alice locally.
- SSH to the remote host to run proxychains wget.
- SSH to the remote host to run proxychains scp.
- Let everything run for a fixed duration.
- Terminate all SSH-launched processes and the local Alice.
- SCP the remote log back to the local logs directory.
"""

from __future__ import absolute_import, print_function

import argparse
import os
import platform
import subprocess
import sys
import time

try:
    from shlex import quote as shell_quote
except ImportError:
    from pipes import quote as shell_quote


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class ManagedProcess(object):
    """Start/stop subprocesses with timeouts and status tracking."""

    def __init__(self, name, cmd, cwd=None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.proc = None
        self.start_error = None
        self.terminated = False
        self.exit_code = None

    def start(self):
        try:
            self.proc = subprocess.Popen(self.cmd, cwd=self.cwd)
        except Exception as exc:
            self.start_error = exc
            return False
        return True

    def poll(self):
        if self.proc is None:
            return None
        return self.proc.poll()

    def stop(self, timeout=5.0):
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            self.exit_code = self.proc.poll()
            return
        self.terminated = True
        try:
            self.proc.terminate()
        except Exception:
            pass
        if _wait_for_exit(self.proc, timeout) is None:
            try:
                self.proc.kill()
            except Exception:
                pass
            _wait_for_exit(self.proc, 1.0)
        self.exit_code = self.proc.poll()


class CommandResult(object):
    """Capture result for synchronous commands."""

    def __init__(self, name, cmd, exit_code=None, error=None):
        self.name = name
        self.cmd = cmd
        self.exit_code = exit_code
        self.error = error


def _wait_for_exit(proc, timeout):
    if proc is None:
        return None
    start = time.time()
    while True:
        code = proc.poll()
        if code is not None:
            return code
        if timeout is not None and time.time() - start >= timeout:
            return None
        time.sleep(0.1)


def shell_join(parts):
    return ' '.join(shell_quote(part) for part in parts)


def require_linux_root():
    if platform.system().lower() != 'linux':
        raise SystemExit('ICMP transport is only supported on Linux')
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('ICMP transport requires root privileges (run as root)')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Remote ICMP Bob + local Alice proxychains runner'
    )
    parser.add_argument(
        '--ssh-bin', default='ssh',
        help='ssh binary (default: ssh)'
    )
    parser.add_argument(
        '--scp-bin', default='scp',
        help='scp binary (default: scp)'
    )
    parser.add_argument(
        '--proxychains-bin', default='proxychains',
        help='proxychains binary (default: proxychains)'
    )
    parser.add_argument(
        '--ssh-identity', default='/root/.ssh/id_rsa',
        help='SSH identity file (default: /root/.ssh/id_rsa)'
    )
    parser.add_argument(
        '--jump-user', default='root',
        help='Jump host user (default: root)'
    )
    parser.add_argument(
        '--jump-host', default='10.254.57.162',
        help='Jump host (default: 10.254.57.162)'
    )
    parser.add_argument(
        '--remote-user', default='root',
        help='Remote SSH user (default: root)'
    )
    parser.add_argument(
        '--remote-host', default='149.28.195.216',
        help='Remote SSH host (default: 149.28.195.216)'
    )
    parser.add_argument(
        '--remote-root', default='/root/signals_from_bob',
        help='Remote project root (default: /root/signals_from_bob)'
    )
    parser.add_argument(
        '--local-root', default=ROOT_DIR,
        help='Local project root (default: script repo root)'
    )
    parser.add_argument(
        '--remote-db-log', default='/var/www/html/server_log.db',
        help='Remote SQLite log path (default: /var/www/html/server_log.db)'
    )
    parser.add_argument(
        '--local-db-log', default=None,
        help='Local SQLite log path (default: <local-root>/logs/client_log.db)'
    )
    parser.add_argument(
        '--log-profile', default='all_events',
        help='Log profile for bob/alice (default: all_events)'
    )
    parser.add_argument(
        '--module', default='socks_server',
        help='Bob module to launch (default: socks_server)'
    )
    parser.add_argument(
        '--max-in-flight', dest='max_in_flight', type=int, default=128,
        help='Max in-flight packets (default: 128)'
    )
    parser.add_argument(
        '--run-seconds', dest='run_seconds', type=int, default=30,
        help='Seconds to let processes run (default: 30)'
    )
    parser.add_argument(
        '--remote-wget-url',
        default='https://testfileorg.netwet.net/500MB-CZIPtestfile.org.zip',
        help='Remote wget URL'
    )
    parser.add_argument(
        '--remote-scp-file', default='/root/500MB-CZIPtestfile.org.zip',
        help='Remote file to scp (default: /root/500MB-CZIPtestfile.org.zip)'
    )
    parser.add_argument(
        '--remote-scp-dest', default='127.0.0.1:/tmp',
        help='Remote scp destination (default: 127.0.0.1:/tmp)'
    )
    parser.add_argument(
        '--remote-scp-identity', default='/root/.ssh/id_ed25519',
        help='Remote scp identity file (default: /root/.ssh/id_ed25519)'
    )
    parser.add_argument(
        '--python-bin', default='python3',
        help='Local python binary (default: python3)'
    )
    parser.add_argument(
        '--remote-python-bin', default=None,
        help='Remote python binary (default: --python-bin)'
    )
    parser.add_argument(
        '--icmp-target', default=None,
        help='ICMP target for Alice (default: remote host)'
    )
    parser.add_argument(
        '--ssh-option', action='append', default=[],
        help='Additional ssh -o options (repeatable)'
    )
    return parser.parse_args()


def build_ssh_base_cmd(args):
    jump = '%s@%s' % (args.jump_user, args.jump_host)
    target = '%s@%s' % (args.remote_user, args.remote_host)
    cmd = [args.ssh_bin, '-i', args.ssh_identity, '-J', jump]
    for opt in args.ssh_option:
        cmd.extend(['-o', opt])
    cmd.append(target)
    return cmd


def build_bob_remote_command(args):
    python_bin = args.remote_python_bin or args.python_bin
    bob_cmd = [
        python_bin, '-m', 'sfb.cli',
        '--role', 'bob',
        '--transport', 'icmp',
        '--module', args.module,
        '--db-log', args.remote_db_log,
        '--log-profile', args.log_profile,
        '--max-in-flight', str(args.max_in_flight),
    ]
    return 'cd %s && git pull && %s' % (
        shell_quote(args.remote_root),
        shell_join(bob_cmd),
    )


def build_wget_remote_command(args):
    return shell_join([
        args.proxychains_bin,
        'wget',
        args.remote_wget_url,
        '-O',
        '/dev/null',
    ])


def build_scp_remote_command(args):
    return shell_join([
        args.proxychains_bin,
        'scp',
        '-i',
        args.remote_scp_identity,
        args.remote_scp_file,
        args.remote_scp_dest,
    ])


def build_alice_local_command(args):
    target = args.icmp_target or args.remote_host
    cmd = [
        args.python_bin, '-m', 'sfb.cli',
        '--role', 'alice',
        '--transport', 'icmp',
        '--icmp-target', target,
        '--log-profile', args.log_profile,
        '--db-log', args.local_db_log,
        '--max-in-flight', str(args.max_in_flight),
    ]
    return cmd


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def run_blocking(name, cmd, cwd=None):
    try:
        exit_code = subprocess.call(cmd, cwd=cwd)
        return CommandResult(name=name, cmd=cmd, exit_code=exit_code)
    except Exception as exc:
        return CommandResult(name=name, cmd=cmd, exit_code=None, error=exc)


def describe_result(result):
    if isinstance(result, ManagedProcess):
        if result.start_error is not None:
            return 'start failed (%s)' % result.start_error
        if result.proc is None:
            return 'not started'
        code = result.poll()
        if code is None and result.terminated:
            return 'terminate requested (still running)'
        if code is None:
            return 'running'
        if result.terminated:
            return 'terminated (rc=%s)' % code
        if code == 0:
            return 'completed (rc=0)'
        return 'failed (rc=%s)' % code
    if result.error is not None:
        return 'failed (%s)' % result.error
    if result.exit_code == 0:
        return 'completed (rc=0)'
    return 'failed (rc=%s)' % result.exit_code


def main():
    args = parse_args()
    require_linux_root()

    if args.local_db_log is None:
        args.local_db_log = os.path.join(args.local_root, 'logs', 'client_log.db')

    ensure_dir(os.path.dirname(args.local_db_log))

    ssh_base_cmd = build_ssh_base_cmd(args)

    bob_remote_cmd = build_bob_remote_command(args)
    wget_remote_cmd = build_wget_remote_command(args)
    scp_remote_cmd = build_scp_remote_command(args)

    bob_proc = ManagedProcess(
        name='remote bob',
        cmd=ssh_base_cmd + [bob_remote_cmd],
    )
    alice_proc = ManagedProcess(
        name='local alice',
        cmd=build_alice_local_command(args),
        cwd=args.local_root,
    )
    wget_proc = ManagedProcess(
        name='remote wget',
        cmd=ssh_base_cmd + [wget_remote_cmd],
    )
    scp_proc = ManagedProcess(
        name='remote scp',
        cmd=ssh_base_cmd + [scp_remote_cmd],
    )

    processes = [bob_proc, alice_proc, wget_proc, scp_proc]

    print('Starting remote Bob...')
    bob_proc.start()
    print('Starting local Alice...')
    alice_proc.start()
    print('Waiting 5 seconds before proxychains commands...')
    time.sleep(5)
    print('Starting remote wget...')
    wget_proc.start()
    print('Starting remote scp...')
    scp_proc.start()

    print('Letting processes run for %d seconds...' % args.run_seconds)
    time.sleep(max(0, args.run_seconds))

    print('Stopping SSH-launched processes and local Alice...')
    for proc in processes:
        proc.stop(timeout=5.0)

    log_dir = os.path.join(args.local_root, 'logs')
    ensure_dir(log_dir)
    remote_log_spec = '%s@%s:%s' % (
        args.remote_user,
        args.remote_host,
        args.remote_db_log,
    )
    scp_cmd = [
        args.scp_bin,
        '-i', args.ssh_identity,
        '-J', '%s@%s' % (args.jump_user, args.jump_host),
        remote_log_spec,
        log_dir,
    ]
    for opt in args.ssh_option:
        scp_cmd.extend(['-o', opt])
    scp_result = run_blocking('fetch remote log', scp_cmd)

    print('')
    print('Summary:')
    for proc in processes:
        print('- %s: %s' % (proc.name, describe_result(proc)))
    print('- %s: %s' % (scp_result.name, describe_result(scp_result)))

    if any(p.start_error is not None for p in processes):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
