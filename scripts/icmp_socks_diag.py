# -*- coding: ascii -*-
"""
ICMP + SOCKS diagnostic harness with live throughput reporting.

- Starts a local HTTP server on port 8888 (configurable) serving test_download_files/.
- Launches Bob (socks) and Alice over ICMP.
- Downloads a file through the SOCKS proxy with a per-second progress bar.
- Optionally runs a direct HTTP baseline to compare against SOCKS.
- Prints per-client stats, aggregate throughput, and a simple throughput timeline.
- Writes SQLite logs to logs/icmp_diag_server_log.db and logs/icmp_diag_client_log.db.

Requirements:
- Linux with root (raw ICMP).
- Kernel ICMP echo replies disabled: sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1
"""

from __future__ import absolute_import, print_function

import argparse
import os
import platform
import posixpath
import socket
import struct
import subprocess
import sys
import threading
import time

try:
    import queue
except ImportError:
    import Queue as queue
try:
    QueueEmpty = queue.Empty  # type: ignore
except AttributeError:
    from Queue import Empty as QueueEmpty  # type: ignore

try:
    from http.server import SimpleHTTPRequestHandler
    from socketserver import TCPServer
    from urllib.parse import unquote
except ImportError:
    from SimpleHTTPServer import SimpleHTTPRequestHandler  # type: ignore
    from SocketServer import TCPServer  # type: ignore
    from urllib import unquote  # type: ignore


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sfb import time_provider

LOG_DIR = os.path.join(ROOT_DIR, 'logs')
SERVER_DB_LOG = os.path.join(LOG_DIR, 'icmp_diag_server_log.db')
CLIENT_DB_LOG = os.path.join(LOG_DIR, 'icmp_diag_client_log.db')
DEFAULT_HTTP_PORT = 8888
DEFAULT_SOCKS_PORT = 1080
DEFAULT_TEST_FILE = '2MB.bin'
PROGRESS_INTERVAL = 1.0


class RootedHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Serve files from a fixed root without changing the process CWD."""

    server_version = "RootedHTTP/0.1"

    def __init__(self, *args, **kwargs):
        self._root = kwargs.pop('root')
        self._quiet = bool(kwargs.pop('quiet', False))
        SimpleHTTPRequestHandler.__init__(self, *args, **kwargs)

    def log_message(self, fmt, *args):
        if self._quiet:
            return
        sys.stdout.write("HTTP %s - - [%s] %s\n" % (
            self.address_string(),
            self.log_date_time_string(),
            fmt % args,
        ))

    def translate_path(self, path):
        """Resolve HTTP paths relative to the configured root directory."""
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        path = posixpath.normpath(unquote(path))
        words = [w for w in path.split('/') if w]
        full_path = self._root
        for word in words:
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word in (os.curdir, os.pardir):
                continue
            full_path = os.path.join(full_path, word)
        return full_path


class ReusableTCPServer(TCPServer):
    allow_reuse_address = True


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
        description='ICMP SOCKS diagnostic harness'
    )
    parser.add_argument(
        '--clients', type=int, default=1,
        help='Number of concurrent SOCKS clients (default: 1)'
    )
    parser.add_argument(
        '--target', default='127.0.0.1',
        help='ICMP target for Alice to reach Bob (default: 127.0.0.1)'
    )
    parser.add_argument(
        '--http-port', type=int, default=DEFAULT_HTTP_PORT,
        help='HTTP server port (default: %d)' % DEFAULT_HTTP_PORT
    )
    parser.add_argument(
        '--socks-port', type=int, default=DEFAULT_SOCKS_PORT,
        help='SOCKS server listen port (default: %d)' % DEFAULT_SOCKS_PORT
    )
    parser.add_argument(
        '--download-file', default=DEFAULT_TEST_FILE,
        help='File name under test_download_files/ to fetch (default: %s)' %
             DEFAULT_TEST_FILE
    )
    parser.add_argument(
        '--timeout', type=float, default=300.0,
        help='Max seconds to wait for setup and downloads (default: 300)'
    )
    parser.add_argument(
        '--db-log-flush', type=float, default=2.0,
        help='SQLite log flush interval for bob/alice (default: 2.0)'
    )
    parser.add_argument(
        '--no-logging', action='store_true',
        help='Disable SFB logging and HTTP request logs'
    )
    parser.add_argument(
        '--no-baseline', action='store_true',
        help='Skip direct HTTP baseline comparison'
    )
    parser.add_argument(
        '--icmp-mtu', type=int, default=None,
        help='Override ICMP payload MTU (passed to sfb CLI)'
    )
    parser.add_argument(
        '--send-rate', type=float, default=None,
        help='Override tunnel send rate for Alice (packets/sec, 0=unlimited)'
    )
    parser.add_argument(
        '--send-burst', type=float, default=None,
        help='Override tunnel send burst for Alice (packets)'
    )
    parser.add_argument(
        '--log-profile', default='socks_throughput_debug',
        help='Log profile to use for Bob/Alice (default: socks_throughput_debug)'
    )
    parser.add_argument(
        '--verbose-cli', action='store_true',
        help='Pass -v to sfb CLI for debug-level logs'
    )
    parser.add_argument(
        '--relay-buffer-size', type=int, default=None,
        help='Override relay-buffer-size (bytes)'
    )
    parser.add_argument(
        '--channel-max-send-buf', type=int, default=None,
        help='Override channel-max-send-buf (bytes)'
    )
    parser.add_argument(
        '--relay-pump-backoff-max', type=float, default=None,
        help='Override relay-pump-backoff-max poll backoff (seconds)'
    )
    parser.add_argument(
        '--non-blocking-poll-timeout', type=float, default=None,
        help='Override non-blocking-poll-timeout (seconds)'
    )
    parser.add_argument(
        '--profile-sfb-dir', default=None,
        help='Write cProfile outputs for Bob/Alice to this directory'
    )
    return parser.parse_args()


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


def ensure_logs(server_path, client_path):
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    for path in (server_path, client_path):
        try:
            os.remove(path)
        except OSError:
            pass


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def resolve_profile_dir(path):
    if not path:
        return None
    if os.path.isabs(path):
        return path
    return os.path.join(ROOT_DIR, path)


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


def start_http_server(root, port, quiet=False):
    handler = lambda *args, **kwargs: RootedHTTPRequestHandler(  # noqa: E731
        *args, root=root, quiet=quiet, **kwargs
    )
    server = ReusableTCPServer(('127.0.0.1', port), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server, thread


def wrap_profile(cmd, profile_dir, label):
    if not profile_dir:
        return cmd
    ensure_dir(profile_dir)
    timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(time_provider.wall_time()))
    profile_path = os.path.join(
        profile_dir,
        '%s_%s_%s.pstats' % (label, timestamp, os.getpid())
    )
    return ['python3', '-m', 'cProfile', '-o', profile_path] + cmd[1:]


def start_bob(socks_port, icmp_mtu=None, log_profile=None, verbose=False,
              relay_buffer_size=None, channel_max_send_buf=None,
              relay_pump_backoff_max=None, non_blocking_poll_timeout=None,
              db_log_path=SERVER_DB_LOG, db_log_flush=None, profile_dir=None):
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'bob',
        '--transport', 'icmp',
    ]
    if verbose:
        cmd.append('-v')
    if db_log_path:
        cmd.extend(['--db-log', db_log_path])
    cmd.extend(['--log-profile', log_profile or 'scp_stalled_icmp_socks'])
    if db_log_path and db_log_flush is not None:
        cmd.extend(['--db-log-flush', str(db_log_flush)])
    if relay_buffer_size is not None:
        cmd.extend(['--relay-buffer-size', str(relay_buffer_size)])
    if channel_max_send_buf is not None:
        cmd.extend(['--channel-max-send-buf', str(channel_max_send_buf)])
    if relay_pump_backoff_max is not None:
        cmd.extend(['--relay-pump-backoff-max', str(relay_pump_backoff_max)])
    if non_blocking_poll_timeout is not None:
        cmd.extend(['--non-blocking-poll-timeout', str(non_blocking_poll_timeout)])
    if icmp_mtu:
        cmd.extend(['--icmp-mtu', str(icmp_mtu)])
    cmd.extend([
        '--module', 'socks',
        'start',
        '--socks-host', '127.0.0.1',
        '--socks-port', str(socks_port),
    ])
    cmd = wrap_profile(cmd, profile_dir, 'bob')
    return ManagedProcess('bob', cmd, cwd=ROOT_DIR)


def start_alice(target, icmp_mtu=None, send_rate=None, send_burst=None,
                log_profile=None, verbose=False, relay_buffer_size=None,
                channel_max_send_buf=None, relay_pump_backoff_max=None,
                non_blocking_poll_timeout=None, db_log_path=CLIENT_DB_LOG,
                db_log_flush=None,
                profile_dir=None):
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'alice',
        '--transport', 'icmp',
    ]
    if verbose:
        cmd.append('-v')
    cmd.extend(['--target', target])
    if db_log_path:
        cmd.extend(['--db-log', db_log_path])
    cmd.extend(['--log-profile', log_profile or 'scp_stalled_icmp_socks'])
    if db_log_path and db_log_flush is not None:
        cmd.extend(['--db-log-flush', str(db_log_flush)])
    if relay_buffer_size is not None:
        cmd.extend(['--relay-buffer-size', str(relay_buffer_size)])
    if channel_max_send_buf is not None:
        cmd.extend(['--channel-max-send-buf', str(channel_max_send_buf)])
    if relay_pump_backoff_max is not None:
        cmd.extend(['--relay-pump-backoff-max', str(relay_pump_backoff_max)])
    if non_blocking_poll_timeout is not None:
        cmd.extend(['--non-blocking-poll-timeout', str(non_blocking_poll_timeout)])
    if icmp_mtu:
        cmd.extend(['--icmp-mtu', str(icmp_mtu)])
    if send_rate is not None:
        cmd.extend(['--send-rate', str(send_rate)])
    if send_burst is not None:
        cmd.extend(['--send-burst', str(send_burst)])
    cmd = wrap_profile(cmd, profile_dir, 'alice')
    return ManagedProcess('alice', cmd, cwd=ROOT_DIR)


def recv_exact(sock, size):
    data = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError('socket closed during recv')
        data.append(chunk)
        remaining -= len(chunk)
    return b''.join(data)


def socks5_connect(proxy_host, proxy_port, target_host, target_port, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    connect_start = time_provider.now()
    sock.connect((proxy_host, proxy_port))
    connect_done = time_provider.now()

    # Method negotiation: only "no auth"
    sock.sendall(b'\x05\x01\x00')
    method_resp = recv_exact(sock, 2)
    if method_resp != b'\x05\x00':
        sock.close()
        raise RuntimeError('SOCKS method negotiation failed: %r' % (method_resp,))

    # Connect request
    target_ip = socket.gethostbyname(target_host)
    req = struct.pack('!BBBB', 5, 1, 0, 1)
    req += socket.inet_aton(target_ip)
    req += struct.pack('!H', target_port)
    sock.sendall(req)

    resp = recv_exact(sock, 4)
    if len(resp) != 4 or resp[0:1] != b'\x05':
        sock.close()
        raise RuntimeError('SOCKS response malformed: %r' % (resp,))
    rep = resp[1]
    atyp = resp[3]
    if rep != 0:
        sock.close()
        raise RuntimeError('SOCKS connect failed with code %d' % rep)
    if atyp == 1:
        addr_len = 4
    elif atyp == 3:
        length_byte = recv_exact(sock, 1)
        addr_len = ord(length_byte)
    elif atyp == 4:
        addr_len = 16
    else:
        sock.close()
        raise RuntimeError('Unsupported ATYP %s' % atyp)
    recv_exact(sock, addr_len)
    recv_exact(sock, 2)
    handshake_done = time_provider.now()

    return sock, (connect_done - connect_start), (handshake_done - connect_done), target_ip


def download_via_socks(client_id, proxy_host, proxy_port, target_host,
                       target_port, request_path, timeout, file_size,
                       result_queue, progress_queue=None):
    metrics = {
        'client_id': client_id,
        'success': False,
        'error': None,
        'bytes': 0,
        'content_length': None,
        'status_code': None,
        'connect_time': None,
        'socks_time': None,
        'ttfb': None,
        'duration': None,
        'throughput_mbps': None,
    }
    sock = None
    next_progress = time_provider.now() + PROGRESS_INTERVAL

    def push_progress():
        if progress_queue is not None:
            progress_queue.put({
                'client_id': client_id,
                'bytes': metrics.get('bytes', 0),
                'ts': time_provider.now(),
            })

    try:
        sock, connect_time, socks_time, _ = socks5_connect(
            proxy_host, proxy_port, target_host, target_port, timeout
        )
        metrics['connect_time'] = connect_time
        metrics['socks_time'] = socks_time

        path = '/' + request_path.lstrip('/')
        request_lines = [
            'GET %s HTTP/1.1' % path,
            'Host: %s:%d' % (target_host, target_port),
            'Connection: close',
            '',
            '',
        ]
        request_bytes = '\r\n'.join(request_lines).encode('ascii')
        request_start = time_provider.now()
        sock.sendall(request_bytes)

        header_buf = b''
        headers_parsed = False
        status_code = None
        content_length = None
        body_bytes = 0
        first_byte_time = None

        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if content_length is not None and body_bytes < content_length:
                    raise RuntimeError(
                        'Timeout before body complete (%d/%d bytes)' %
                        (body_bytes, content_length)
                    )
                break
            if not chunk:
                if content_length is not None and body_bytes < content_length:
                    raise RuntimeError(
                        'Socket closed before body complete (%d/%d bytes)' %
                        (body_bytes, content_length)
                    )
                break
            if first_byte_time is None:
                first_byte_time = time_provider.now()
            if not headers_parsed:
                header_buf += chunk
                if b'\r\n\r\n' not in header_buf:
                    continue
                header_bytes, body = header_buf.split(b'\r\n\r\n', 1)
                header_buf = b''
                lines = header_bytes.split(b'\r\n')
                if not lines:
                    raise RuntimeError('HTTP response missing status line')
                status_line = lines[0].decode('iso-8859-1')
                parts = status_line.split()
                if len(parts) < 2:
                    raise RuntimeError('HTTP status malformed: %s' % status_line)
                try:
                    status_code = int(parts[1])
                except ValueError:
                    raise RuntimeError('HTTP status code not int: %s' % status_line)
                for raw in lines[1:]:
                    try:
                        key, value = raw.split(b':', 1)
                    except ValueError:
                        continue
                    if key.strip().lower() == b'content-length':
                        try:
                            content_length = int(value.strip())
                        except ValueError:
                            content_length = None
                body_bytes += len(body)
                headers_parsed = True
            else:
                body_bytes += len(chunk)
            metrics['bytes'] = body_bytes
            if content_length is not None and body_bytes >= content_length:
                break
            if time_provider.now() >= next_progress:
                push_progress()
                next_progress = time_provider.now() + PROGRESS_INTERVAL

        end_time = time_provider.now()
        if status_code != 200:
            raise RuntimeError('HTTP status %s (expected 200)' % status_code)
        if content_length is not None and body_bytes != content_length:
            raise RuntimeError(
                'Content-Length mismatch: expected %s got %s' %
                (content_length, body_bytes)
            )
        if first_byte_time is None:
            raise RuntimeError('No response bytes received')

        metrics['success'] = True
        metrics['status_code'] = status_code
        metrics['content_length'] = content_length
        metrics['bytes'] = body_bytes
        metrics['ttfb'] = first_byte_time - request_start
        metrics['duration'] = end_time - first_byte_time
        if metrics['duration'] > 0:
            metrics['throughput_mbps'] = (body_bytes / metrics['duration']) / (1024 * 1024)
    except Exception as exc:
        metrics['error'] = str(exc)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        metrics['bytes'] = metrics.get('bytes', 0)
        push_progress()
        result_queue.put(metrics)


def render_progress(status_bytes, expected_total, start_time, client_count, last_len):
    total_bytes = sum(status_bytes.values()) if status_bytes else 0
    elapsed = max(time_provider.now() - start_time, 0.001)
    rate_mbps = (total_bytes / elapsed) / (1024 * 1024)
    parts = ['Progress:']
    if expected_total:
        percent = (100.0 * total_bytes / expected_total)
        parts.append('%d/%d bytes' % (total_bytes, expected_total))
        parts.append('(%.1f%%)' % percent)
    else:
        parts.append('%d bytes' % total_bytes)
    parts.append('rate=%.3f MB/s' % rate_mbps)
    parts.append('clients=%d' % client_count)
    line = ' '.join(parts)
    padding = ''
    if last_len > len(line):
        padding = ' ' * (last_len - len(line))
    sys.stdout.write('\r' + line + padding)
    sys.stdout.flush()
    return len(line)


def run_downloads(client_count, proxy_host, proxy_port, target_host,
                  target_port, filename, timeout, file_size):
    result_q = queue.Queue()
    progress_q = queue.Queue()
    threads = []
    start = time_provider.now()
    for idx in range(client_count):
        t = threading.Thread(
            target=download_via_socks,
            args=(
                idx,
                proxy_host,
                proxy_port,
                target_host,
                target_port,
                filename,
                timeout,
                file_size,
                result_q,
                progress_q,
            )
        )
        t.daemon = True
        t.start()
        threads.append((idx, t))

    status_bytes = {}
    expected_total = None
    if file_size:
        expected_total = file_size * client_count
    last_render_len = 0
    progress_log = []
    deadline = start + timeout
    while True:
        alive = False
        for _, t in threads:
            if t.is_alive():
                alive = True
                break
        try:
            update = progress_q.get(timeout=PROGRESS_INTERVAL)
            cid = update.get('client_id')
            bval = update.get('bytes', 0)
            ts = update.get('ts', time_provider.now())
            status_bytes[cid] = bval
            progress_log.append((ts, sum(status_bytes.values())))
        except QueueEmpty:
            pass
        last_render_len = render_progress(
            status_bytes,
            expected_total,
            start,
            client_count,
            last_render_len,
        )
        if not alive or time_provider.now() >= deadline:
            while True:
                try:
                    update = progress_q.get_nowait()
                    cid = update.get('client_id')
                    bval = update.get('bytes', 0)
                    ts = update.get('ts', time_provider.now())
                    status_bytes[cid] = bval
                    progress_log.append((ts, sum(status_bytes.values())))
                except QueueEmpty:
                    break
            last_render_len = render_progress(
                status_bytes,
                expected_total,
                start,
                client_count,
                last_render_len,
            )
            sys.stdout.write('\n')
            sys.stdout.flush()
            break

    for _, t in threads:
        t.join(timeout)
    elapsed = time_provider.now() - start
    results = []
    while not result_q.empty():
        results.append(result_q.get())
    seen = set(r.get('client_id') for r in results)
    for idx, t in threads:
        if idx not in seen:
            results.append({
                'client_id': idx,
                'success': False,
                'error': 'timeout waiting for client thread',
                'bytes': 0,
                'status_code': None,
            })
    progress_log.sort(key=lambda x: x[0])
    return results, elapsed, progress_log, start


def summarize_results(results, total_elapsed):
    successes = [r for r in results if r.get('success')]
    total_bytes = sum(r.get('bytes', 0) for r in successes)
    aggregate_rate = None
    if total_elapsed > 0:
        aggregate_rate = (total_bytes / total_elapsed) / (1024 * 1024)
    avg_throughput = None
    if successes:
        avg_throughput = sum(
            r.get('throughput_mbps', 0.0) or 0.0 for r in successes
        ) / float(len(successes))
    summary = {
        'clients': len(results),
        'successes': len(successes),
        'failures': len(results) - len(successes),
        'total_bytes': total_bytes,
        'aggregate_rate_mbps': aggregate_rate,
        'avg_client_rate_mbps': avg_throughput,
    }
    return summary


def compute_timeline(progress_log, start_time):
    timeline = []
    last_bytes = 0
    last_ts = start_time
    for ts, total_bytes in progress_log:
        if ts < last_ts:
            continue
        dt = ts - last_ts
        delta = total_bytes - last_bytes
        if dt > 0:
            rate_mbps = (delta / dt) / (1024 * 1024)
            timeline.append((ts - start_time, total_bytes, rate_mbps))
        last_bytes = total_bytes
        last_ts = ts
    return timeline


def download_direct_http(host, port, request_path, timeout, file_size):
    metrics = {
        'success': False,
        'error': None,
        'bytes': 0,
        'status_code': None,
        'ttfb': None,
        'duration': None,
        'throughput_mbps': None,
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        start = time_provider.now()
        sock.connect((host, port))
        connect_time = time_provider.now() - start
        path = '/' + request_path.lstrip('/')
        req = [
            'GET %s HTTP/1.1' % path,
            'Host: %s:%d' % (host, port),
            'Connection: close',
            '',
            '',
        ]
        sock.sendall('\r\n'.join(req).encode('ascii'))
        header_buf = b''
        headers_parsed = False
        status_code = None
        content_length = None
        body_bytes = 0
        first_byte_time = None
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if content_length is not None and body_bytes < content_length:
                    raise RuntimeError(
                        'Timeout before body complete (%d/%d bytes)' %
                        (body_bytes, content_length)
                    )
                break
            if not chunk:
                if content_length is not None and body_bytes < content_length:
                    raise RuntimeError(
                        'Socket closed before body complete (%d/%d bytes)' %
                        (body_bytes, content_length)
                    )
                break
            if first_byte_time is None:
                first_byte_time = time_provider.now()
            if not headers_parsed:
                header_buf += chunk
                if b'\r\n\r\n' not in header_buf:
                    continue
                header_bytes, body = header_buf.split(b'\r\n\r\n', 1)
                header_buf = b''
                lines = header_bytes.split(b'\r\n')
                status_line = lines[0].decode('iso-8859-1')
                parts = status_line.split()
                status_code = int(parts[1])
                for raw in lines[1:]:
                    try:
                        key, value = raw.split(b':', 1)
                    except ValueError:
                        continue
                    if key.strip().lower() == b'content-length':
                        try:
                            content_length = int(value.strip())
                        except ValueError:
                            content_length = None
                body_bytes += len(body)
                headers_parsed = True
            else:
                body_bytes += len(chunk)
            if content_length is not None and body_bytes >= content_length:
                break
        end = time_provider.now()
        metrics['status_code'] = status_code
        metrics['bytes'] = body_bytes
        if status_code == 200:
            metrics['success'] = True
        else:
            metrics['error'] = 'HTTP %s' % status_code
        metrics['ttfb'] = (first_byte_time - start) if first_byte_time else None
        metrics['duration'] = (end - first_byte_time) if first_byte_time else None
        if metrics['duration'] and metrics['duration'] > 0:
            metrics['throughput_mbps'] = (body_bytes / metrics['duration']) / (1024 * 1024)
        metrics['connect_time'] = connect_time
    except Exception as exc:
        metrics['error'] = str(exc)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return metrics


def shutdown(http_server, bob, alice):
    try:
        if http_server:
            http_server.shutdown()
    except Exception:
        pass
    try:
        if http_server:
            http_server.server_close()
    except Exception:
        pass
    for proc in (alice, bob):
        try:
            proc.stop()
        except Exception:
            pass


def main():
    args = parse_args()
    if args.clients < 1:
        raise SystemExit('At least one client is required')

    require_linux_root()
    quiet_http = False
    log_profile = args.log_profile
    server_db_log = SERVER_DB_LOG
    client_db_log = CLIENT_DB_LOG
    if args.no_logging:
        quiet_http = True
        log_profile = 'no_logging'
        server_db_log = None
        client_db_log = None
    else:
        ensure_logs(SERVER_DB_LOG, CLIENT_DB_LOG)
    profile_dir = resolve_profile_dir(args.profile_sfb_dir)

    http_root = os.path.join(ROOT_DIR, 'test_download_files')
    download_path = os.path.join(http_root, args.download_file)
    if not os.path.isfile(download_path):
        raise SystemExit('Download file not found: %s' % download_path)
    file_size = os.path.getsize(download_path)

    http_server = None
    bob = None
    alice = None
    try:
        http_server, _ = start_http_server(http_root, args.http_port, quiet=quiet_http)

        bob = start_bob(
            args.socks_port,
            icmp_mtu=args.icmp_mtu,
            log_profile=log_profile,
            verbose=args.verbose_cli,
            relay_buffer_size=args.relay_buffer_size,
            channel_max_send_buf=args.channel_max_send_buf,
            relay_pump_backoff_max=args.relay_pump_backoff_max,
            non_blocking_poll_timeout=args.non_blocking_poll_timeout,
            db_log_path=server_db_log,
            db_log_flush=args.db_log_flush,
            profile_dir=profile_dir,
        )
        alice = start_alice(
            args.target,
            icmp_mtu=args.icmp_mtu,
            send_rate=args.send_rate,
            send_burst=args.send_burst,
            log_profile=log_profile,
            verbose=args.verbose_cli,
            relay_buffer_size=args.relay_buffer_size,
            channel_max_send_buf=args.channel_max_send_buf,
            relay_pump_backoff_max=args.relay_pump_backoff_max,
            non_blocking_poll_timeout=args.non_blocking_poll_timeout,
            db_log_path=client_db_log,
            db_log_flush=args.db_log_flush,
            profile_dir=profile_dir,
        )
        bob.start()
        time_provider.sleep(0.2)
        alice.start()

        deadline = time_provider.now() + args.timeout
        socks_ready = wait_for_port('127.0.0.1', args.socks_port, deadline, proc=bob)
        if not socks_ready:
            raise SystemExit('SOCKS server did not become ready before timeout')

        remaining = deadline - time_provider.now()
        if remaining <= 0:
            raise SystemExit('Timeout expired before downloads could start')

        results, elapsed, progress_log, start_time = run_downloads(
            args.clients,
            '127.0.0.1',
            args.socks_port,
            '127.0.0.1',
            args.http_port,
            args.download_file,
            remaining,
            file_size,
        )
        summary = summarize_results(results, elapsed)
        timeline = compute_timeline(progress_log, start_time)
        peak_rate = max([r[2] for r in timeline], default=0.0)

        print('\n=== SOCKS Download Results ===')
        for r in sorted(results, key=lambda x: x.get('client_id', 0)):
            line = [
                'client=%s' % r.get('client_id'),
                'success=%s' % r.get('success'),
                'bytes=%s' % r.get('bytes'),
                'status=%s' % r.get('status_code'),
            ]
            if r.get('throughput_mbps') is not None:
                line.append('throughput_mbps=%.3f' % r['throughput_mbps'])
            if r.get('ttfb') is not None:
                line.append('ttfb=%.3f' % r['ttfb'])
            if r.get('duration') is not None:
                line.append('duration=%.3f' % r['duration'])
            if r.get('error'):
                line.append('error=%s' % r['error'])
            print(' '.join(line))

        print('\n=== SOCKS Summary ===')
        print('clients=%d successes=%d failures=%d total_bytes=%d aggregate_rate_mbps=%s avg_client_rate_mbps=%s peak_rate_mbps=%.3f' % (
            summary['clients'],
            summary['successes'],
            summary['failures'],
            summary['total_bytes'],
            ('%.3f' % summary['aggregate_rate_mbps']) if summary['aggregate_rate_mbps'] is not None else 'n/a',
            ('%.3f' % summary['avg_client_rate_mbps']) if summary['avg_client_rate_mbps'] is not None else 'n/a',
            peak_rate,
        ))

        if timeline:
            print('\n=== Throughput Timeline (seconds, bytes, MB/s) ===')
            # Show first few and last few points to keep output short.
            preview = timeline[:3] + (['...'] if len(timeline) > 6 else []) + timeline[-3:]
            for entry in preview:
                if entry == '...':
                    print('...')
                    continue
                ts_rel, bytes_now, rate = entry
                print('t=%.1f bytes=%d rate=%.3f MB/s' % (ts_rel, bytes_now, rate))

        if not args.no_baseline:
            print('\n=== Direct HTTP Baseline ===')
            baseline = download_direct_http(
                '127.0.0.1',
                args.http_port,
                args.download_file,
                timeout=remaining,
                file_size=file_size,
            )
            print('success=%s status=%s bytes=%s connect_time=%.3f ttfb=%s duration=%s throughput_mbps=%s error=%s' % (
                baseline.get('success'),
                baseline.get('status_code'),
                baseline.get('bytes'),
                baseline.get('connect_time', 0.0) or 0.0,
                ('%.3f' % baseline['ttfb']) if baseline.get('ttfb') is not None else 'n/a',
                ('%.3f' % baseline['duration']) if baseline.get('duration') is not None else 'n/a',
                ('%.3f' % baseline['throughput_mbps']) if baseline.get('throughput_mbps') is not None else 'n/a',
                baseline.get('error'),
            ))

        print('\nLogs: server=%s client=%s' % (SERVER_DB_LOG, CLIENT_DB_LOG))
        return 0
    finally:
        shutdown(http_server, bob, alice)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        sys.exit(1)
