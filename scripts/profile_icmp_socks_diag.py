# -*- coding: ascii -*-
"""
Run icmp_socks_diag with profiling and store results under profile_results/.
"""

from __future__ import absolute_import, print_function

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT_DIR, 'profile_results')

try:
    text_type = unicode
except NameError:
    text_type = str
try:
    binary_type = bytes
except NameError:
    binary_type = str


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


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


def _build_ssh_base_cmd(args):
    if not args.remote_host:
        return None
    cmd = [args.ssh_bin]
    if args.ssh_identity:
        cmd.extend(['-i', args.ssh_identity])
    for opt in args.ssh_option:
        cmd.extend(['-o', opt])
    target = args.remote_host
    if args.remote_user:
        target = '%s@%s' % (args.remote_user, args.remote_host)
    cmd.append(target)
    return cmd


def _list_remote_python_processes(ssh_cmd):
    python_procs = {}
    if not ssh_cmd:
        return python_procs
    try:
        output = subprocess.check_output(ssh_cmd + ['ps', '-eo', 'pid,comm'])
    except Exception as exc:
        raise SystemExit('Failed to query remote processes: %s' % exc)
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


def _unexpected_local_python(allowed_pids, allowed_pgid):
    python_procs = _list_local_python_processes()
    unexpected = {}
    for pid, info in python_procs.items():
        if pid in allowed_pids:
            continue
        if allowed_pgid is not None and os.name != 'nt':
            try:
                if os.getpgid(pid) == allowed_pgid:
                    continue
            except Exception:
                pass
        unexpected[pid] = info
    return unexpected


def _terminate_process_group(proc, timeout=5.0):
    if proc is None:
        return
    if proc.poll() is not None:
        return
    if os.name != 'nt':
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    else:
        try:
            proc.terminate()
        except Exception:
            pass
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if os.name != 'nt':
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        try:
            proc.kill()
        except Exception:
            pass


def _install_signal_handlers(cleanup_func):
    def _handler(_signum, _frame):
        cleanup_func()
        raise SystemExit(1)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def _parse_args():
    parser = argparse.ArgumentParser(
        description='Profile icmp_socks_diag with strict process checks'
    )
    parser.add_argument(
        '--remote-host', default=None,
        help='Remote host for python process checks'
    )
    parser.add_argument(
        '--remote-user', default=None,
        help='Remote SSH user (default: current)'
    )
    parser.add_argument(
        '--ssh-bin', default='ssh',
        help='ssh binary for remote checks (default: ssh)'
    )
    parser.add_argument(
        '--ssh-identity', default=None,
        help='SSH identity file for remote checks'
    )
    parser.add_argument(
        '--ssh-option', action='append', default=[],
        help='Additional ssh -o options (repeatable)'
    )
    return parser.parse_args()


def _preflight_process_checks(ssh_cmd):
    unexpected_local = _unexpected_local_python(set([os.getpid()]), None)
    if unexpected_local:
        raise SystemExit(
            'Unexpected local python processes: %s' %
            _format_python_processes(unexpected_local)
        )
    if ssh_cmd:
        unexpected_remote = _list_remote_python_processes(ssh_cmd)
        if unexpected_remote:
            raise SystemExit(
                'Unexpected remote python processes: %s' %
                _format_python_processes(unexpected_remote)
            )


def _wait_with_checks(proc, ssh_cmd, interval=1.0):
    allowed_pids = set([os.getpid(), proc.pid])
    allowed_pgid = None
    if os.name != 'nt':
        try:
            allowed_pgid = os.getpgid(proc.pid)
        except Exception:
            allowed_pgid = None
    last_check = 0.0
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        now = time.time()
        if now - last_check >= interval:
            last_check = now
            unexpected_local = _unexpected_local_python(allowed_pids, allowed_pgid)
            if unexpected_local:
                raise RuntimeError(
                    'Unexpected local python processes: %s' %
                    _format_python_processes(unexpected_local)
                )
            if ssh_cmd:
                unexpected_remote = _list_remote_python_processes(ssh_cmd)
                if unexpected_remote:
                    raise RuntimeError(
                        'Unexpected remote python processes: %s' %
                        _format_python_processes(unexpected_remote)
                    )
        time.sleep(0.1)


def main():
    args = _parse_args()
    ssh_cmd = _build_ssh_base_cmd(args)
    _preflight_process_checks(ssh_cmd)
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
        '--target', '127.0.0.1',
        '--profile-sfb-dir', RESULTS_DIR,
    ]
    sys.stdout.write('Writing profile to: %s\n' % profile_path)
    sys.stdout.write('Writing Bob/Alice profiles to: %s\n' % RESULTS_DIR)
    preexec_fn = None
    if os.name != 'nt':
        preexec_fn = os.setsid
    proc = subprocess.Popen(cmd, cwd=ROOT_DIR, preexec_fn=preexec_fn)
    sys.stdout.write('Started icmp_socks_diag pid=%s\n' % proc.pid)

    def _cleanup():
        _terminate_process_group(proc)

    atexit.register(_cleanup)
    _install_signal_handlers(_cleanup)
    try:
        return _wait_with_checks(proc, ssh_cmd)
    except RuntimeError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return 1
    finally:
        _cleanup()


if __name__ == '__main__':
    sys.exit(main())
