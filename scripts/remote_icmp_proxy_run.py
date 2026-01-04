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
import atexit
import os
import platform
import signal
import subprocess
import sys
import time

try:
    from shlex import quote as shell_quote
except ImportError:
    from pipes import quote as shell_quote


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


try:
    text_type = unicode
except NameError:
    text_type = str
try:
    binary_type = bytes
except NameError:
    binary_type = str


def _decode_text(data):
    if isinstance(data, text_type):
        return data
    if isinstance(data, binary_type):
        encoding = 'mbcs' if os.name == 'nt' else 'utf-8'
        try:
            return data.decode(encoding)
        except Exception:
            return data.decode('utf-8', 'replace')
    return text_type(data)


def _is_python_name(value):
    if not value:
        return False
    return 'python' in value.lower()


def _read_file(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except (IOError, OSError):
        return b''


def _list_proc_python_processes():
    python_procs = {}
    proc_root = '/proc'
    if not os.path.isdir(proc_root):
        return python_procs
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        pid = int(entry)
        comm_path = os.path.join(proc_root, entry, 'comm')
        cmdline_path = os.path.join(proc_root, entry, 'cmdline')
        comm = _decode_text(_read_file(comm_path)).strip()
        cmdline = _decode_text(_read_file(cmdline_path)).replace('\x00', ' ').strip()
        if _is_python_name(comm) or _is_python_name(cmdline):
            python_procs[pid] = {'name': comm, 'cmdline': cmdline}
    return python_procs


def _list_ps_python_processes():
    python_procs = {}
    try:
        output = subprocess.check_output(['ps', '-eo', 'pid,comm'])
    except Exception:
        return python_procs
    text = _decode_text(output)
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        name = parts[1].strip()
        if _is_python_name(name):
            python_procs[pid] = {'name': name, 'cmdline': ''}
    return python_procs


def _list_windows_python_processes():
    python_procs = {}
    try:
        output = subprocess.check_output(['tasklist', '/FO', 'LIST', '/NH'])
    except Exception:
        return python_procs
    text = _decode_text(output)
    name = None
    pid = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if name and pid:
                if _is_python_name(name):
                    python_procs[int(pid)] = {'name': name, 'cmdline': ''}
            name = None
            pid = None
            continue
        lower = line.lower()
        if lower.startswith('image name:'):
            name = line.split(':', 1)[1].strip()
        elif lower.startswith('pid:'):
            pid = line.split(':', 1)[1].strip()
    if name and pid:
        if _is_python_name(name):
            python_procs[int(pid)] = {'name': name, 'cmdline': ''}
    return python_procs


def _list_local_python_processes():
    if os.name == 'nt':
        return _list_windows_python_processes()
    python_procs = _list_proc_python_processes()
    if python_procs:
        return python_procs
    return _list_ps_python_processes()


def _list_remote_python_processes(ssh_base_cmd):
    python_procs = {}
    if not ssh_base_cmd:
        return python_procs
    try:
        output = subprocess.check_output(ssh_base_cmd + ['ps', '-eo', 'pid,comm'])
    except Exception as exc:
        raise RuntimeError('Failed to query remote processes: %s' % exc)
    text = _decode_text(output)
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        name = parts[1].strip()
        if _is_python_name(name):
            python_procs[pid] = {'name': name, 'cmdline': ''}
    return python_procs


def _format_python_processes(procs):
    lines = []
    for pid in sorted(procs):
        info = procs[pid]
        name = info.get('name') or ''
        cmdline = info.get('cmdline') or ''
        detail = name
        if cmdline and cmdline != name:
            detail = '%s (%s)' % (name, cmdline)
        if detail:
            lines.append('pid=%s %s' % (pid, detail))
        else:
            lines.append('pid=%s' % pid)
    return ', '.join(lines)


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


class RemoteManagedProcess(ManagedProcess):
    """Start/stop remote subprocesses with PID tracking."""

    def __init__(self, name, ssh_base_cmd, pre_cmds, exec_cmd, pid_file,
                 pid_timeout=10.0, cwd=None):
        ManagedProcess.__init__(self, name=name, cmd=None, cwd=cwd)
        self.ssh_base_cmd = ssh_base_cmd
        self.pre_cmds = pre_cmds or []
        self.exec_cmd = exec_cmd
        self.pid_file = pid_file
        self.pid_timeout = pid_timeout
        self.remote_pid = None

    def _build_remote_command(self):
        return build_remote_wrapped_command(
            self.pre_cmds,
            self.exec_cmd,
            self.pid_file,
        )

    def start(self):
        self.cmd = self.ssh_base_cmd + [self._build_remote_command()]
        if not ManagedProcess.start(self):
            return False
        self.remote_pid = wait_for_remote_pid(
            self.ssh_base_cmd,
            self.pid_file,
            self.pid_timeout,
            proc=self.proc,
        )
        if self.remote_pid is None:
            self.start_error = RuntimeError('remote pid not found')
            self.stop(timeout=2.0)
            return False
        sys.stdout.write('Started %s pid=%s\n' % (self.name, self.remote_pid))
        return True

    def stop(self, timeout=5.0):
        if self.remote_pid is not None:
            remote_kill_pid(
                self.ssh_base_cmd,
                self.remote_pid,
                pid_file=self.pid_file,
            )
        ManagedProcess.stop(self, timeout)


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


def build_remote_pid_file(label, local_pid):
    safe_label = label.replace(' ', '_')
    return '/tmp/sfb_remote_icmp_proxy_run_%s_%s.pid' % (safe_label, local_pid)


def build_remote_wrapped_command(pre_cmds, exec_cmd, pid_file):
    script_parts = ['echo $$ > %s' % shell_quote(pid_file)]
    if pre_cmds:
        script_parts.extend(pre_cmds)
    script_parts.append('exec %s' % exec_cmd)
    script = ' && '.join(script_parts)
    return 'sh -c %s' % shell_quote(script)


def read_remote_pid_once(ssh_base_cmd, pid_file):
    try:
        output = subprocess.check_output(ssh_base_cmd + ['cat', pid_file])
    except Exception:
        return None
    text = _decode_text(output).strip()
    if text.isdigit():
        return int(text)
    return None


def wait_for_remote_pid(ssh_base_cmd, pid_file, timeout, proc=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return None
        pid = read_remote_pid_once(ssh_base_cmd, pid_file)
        if pid is not None:
            return pid
        time.sleep(0.2)
    return None


def remote_kill_pid(ssh_base_cmd, pid, pid_file=None):
    if pid is None:
        return
    script = (
        'if kill -0 %(pid)d 2>/dev/null; then '
        'kill -TERM %(pid)d 2>/dev/null || true; '
        'i=0; while [ $i -lt 50 ]; do '
        'if ! kill -0 %(pid)d 2>/dev/null; then exit 0; fi; '
        'sleep 0.1; i=$((i+1)); done; '
        'kill -KILL %(pid)d 2>/dev/null || true; '
        'fi'
    ) % {'pid': pid}
    if pid_file:
        script = '%s; rm -f %s 2>/dev/null || true' % (
            script,
            shell_quote(pid_file),
        )
    try:
        subprocess.call(ssh_base_cmd + ['sh -c %s' % shell_quote(script)])
    except Exception:
        return


def _unexpected_local_python(allowed_pids):
    python_procs = _list_local_python_processes()
    unexpected = {}
    for pid, info in python_procs.items():
        if pid in allowed_pids:
            continue
        unexpected[pid] = info
    return unexpected


def _unexpected_remote_python(ssh_base_cmd, allowed_pids):
    python_procs = _list_remote_python_processes(ssh_base_cmd)
    unexpected = {}
    for pid, info in python_procs.items():
        if pid in allowed_pids:
            continue
        unexpected[pid] = info
    return unexpected


def enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids):
    unexpected_local = _unexpected_local_python(allowed_local_pids)
    if unexpected_local:
        raise RuntimeError(
            'Unexpected local python processes: %s' %
            _format_python_processes(unexpected_local)
        )
    if ssh_base_cmd:
        unexpected_remote = _unexpected_remote_python(ssh_base_cmd, allowed_remote_pids)
        if unexpected_remote:
            raise RuntimeError(
                'Unexpected remote python processes: %s' %
                _format_python_processes(unexpected_remote)
            )


def sleep_with_checks(duration, interval, ssh_base_cmd, allowed_local_pids,
                      allowed_remote_pids):
    deadline = time.time() + max(0, duration)
    while time.time() < deadline:
        enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids)
        remaining = deadline - time.time()
        time.sleep(min(interval, max(0.0, remaining)))


def install_signal_handlers(cleanup_func):
    def _handler(_signum, _frame):
        cleanup_func()
        raise SystemExit(1)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


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
    pre_cmds = [
        'cd %s' % shell_quote(args.remote_root),
        'git pull',
    ]
    return pre_cmds, shell_join(bob_cmd)


def build_wget_remote_command(args):
    return [], shell_join([
        args.proxychains_bin,
        'wget',
        args.remote_wget_url,
        '-O',
        '/dev/null',
    ])


def build_scp_remote_command(args):
    return [], shell_join([
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
    local_pid = os.getpid()
    allowed_local_pids = set([local_pid])
    allowed_remote_pids = set()

    bob_pre_cmds, bob_exec_cmd = build_bob_remote_command(args)
    wget_pre_cmds, wget_exec_cmd = build_wget_remote_command(args)
    scp_pre_cmds, scp_exec_cmd = build_scp_remote_command(args)

    bob_proc = RemoteManagedProcess(
        name='remote bob',
        ssh_base_cmd=ssh_base_cmd,
        pre_cmds=bob_pre_cmds,
        exec_cmd=bob_exec_cmd,
        pid_file=build_remote_pid_file('bob', local_pid),
        pid_timeout=30.0,
    )
    alice_proc = ManagedProcess(
        name='local alice',
        cmd=build_alice_local_command(args),
        cwd=args.local_root,
    )
    wget_proc = RemoteManagedProcess(
        name='remote wget',
        ssh_base_cmd=ssh_base_cmd,
        pre_cmds=wget_pre_cmds,
        exec_cmd=wget_exec_cmd,
        pid_file=build_remote_pid_file('wget', local_pid),
        pid_timeout=10.0,
    )
    scp_proc = RemoteManagedProcess(
        name='remote scp',
        ssh_base_cmd=ssh_base_cmd,
        pre_cmds=scp_pre_cmds,
        exec_cmd=scp_exec_cmd,
        pid_file=build_remote_pid_file('scp', local_pid),
        pid_timeout=10.0,
    )

    processes = [bob_proc, alice_proc, wget_proc, scp_proc]

    cleanup_state = {'done': False}

    def cleanup():
        if cleanup_state['done']:
            return
        cleanup_state['done'] = True
        print('Stopping SSH-launched processes and local Alice...')
        for proc in processes:
            try:
                proc.stop(timeout=5.0)
            except Exception:
                pass

    atexit.register(cleanup)
    install_signal_handlers(cleanup)

    exit_code = 0
    error_message = None
    try:
        enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids)

        print('Starting remote Bob...')
        if not bob_proc.start():
            raise RuntimeError('Failed to start remote Bob: %s' % bob_proc.start_error)
        allowed_remote_pids = set([bob_proc.remote_pid])
        enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids)

        print('Waiting 2 seconds before starting local Alice...')
        sleep_with_checks(
            2,
            0.5,
            ssh_base_cmd,
            allowed_local_pids,
            allowed_remote_pids,
        )

        print('Starting local Alice...')
        if not alice_proc.start():
            raise RuntimeError('Failed to start local Alice: %s' % alice_proc.start_error)
        if alice_proc.proc is not None:
            allowed_local_pids.add(alice_proc.proc.pid)
        enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids)

        print('Waiting 5 seconds before proxychains commands...')
        sleep_with_checks(
            5,
            0.5,
            ssh_base_cmd,
            allowed_local_pids,
            allowed_remote_pids,
        )

        print('Starting remote wget...')
        if not wget_proc.start():
            raise RuntimeError('Failed to start remote wget: %s' % wget_proc.start_error)
        print('Starting remote scp...')
        if not scp_proc.start():
            raise RuntimeError('Failed to start remote scp: %s' % scp_proc.start_error)
        enforce_python_clean(ssh_base_cmd, allowed_local_pids, allowed_remote_pids)

        print('Letting processes run for %d seconds...' % args.run_seconds)
        sleep_with_checks(
            args.run_seconds,
            1.0,
            ssh_base_cmd,
            allowed_local_pids,
            allowed_remote_pids,
        )
    except Exception as exc:
        exit_code = 1
        error_message = str(exc)
    finally:
        cleanup()

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

    if error_message:
        sys.stderr.write('ERROR: %s\n' % error_message)

    print('')
    print('Summary:')
    for proc in processes:
        print('- %s: %s' % (proc.name, describe_result(proc)))
    print('- %s: %s' % (scp_result.name, describe_result(scp_result)))

    if any(p.start_error is not None for p in processes):
        return 1
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
