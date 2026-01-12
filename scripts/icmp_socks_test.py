# -*- coding: ascii -*-
"""
Stand up an ICMP tunnel with a SOCKS server and download a file through it.

Flow:
- Start a local HTTP server on port 8888 serving test_download_files/.
- Launch Bob with ICMP transport and the socks module.
- Launch Alice with ICMP transport pointing at Bob.
- Optionally run N concurrent SOCKS clients (default: 1) that fetch a file
  from the HTTP server through the SOCKS proxy.
- Report download success/failure, transfer rate, and timing metrics.
- Write Bob/Alice SQLite logs to logs/icmp_server_log.db and
  logs/icmp_client_log.db (overwritten each run).

Requirements:
- Linux with root privileges (ICMP transport uses raw sockets).
- Kernel echo replies must be disabled:
    sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1
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
SERVER_DB_LOG = os.path.join(LOG_DIR, 'icmp_server_log.db')
CLIENT_DB_LOG = os.path.join(LOG_DIR, 'icmp_client_log.db')
DEFAULT_HTTP_PORT = 8888
DEFAULT_SOCKS_PORT = 1080
DEFAULT_TEST_FILE = '2MB.bin'
PROGRESS_INTERVAL = 1.0


class RootedHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Serve files from a fixed root without changing the process CWD."""

    server_version = "RootedHTTP/0.1"

    def __init__(self, *args, **kwargs):
        self._root = kwargs.pop('root')
        SimpleHTTPRequestHandler.__init__(self, *args, **kwargs)

    def log_message(self, fmt, *args):
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
        description='ICMP SOCKS tunnel downloader'
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


def ensure_logs():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    for path in (SERVER_DB_LOG, CLIENT_DB_LOG):
        try:
            os.remove(path)
        except OSError:
            pass


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


def start_http_server(root, port):
    handler = lambda *args, **kwargs: RootedHTTPRequestHandler(  # noqa: E731
        *args, root=root, **kwargs
    )
    server = ReusableTCPServer(('127.0.0.1', port), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server, thread


def start_bob(socks_port):
    listen_addr = '127.0.0.1:%d' % socks_port
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'bob',
        '--transport', 'icmp',
        '--db-log', SERVER_DB_LOG,
        '--log-profile', 'scp_stalled_icmp_socks',
        '--module', 'socks',
        '--socks-listen', listen_addr,
    ]
    return ManagedProcess('bob', cmd, cwd=ROOT_DIR)


def start_alice(target):
    cmd = [
        'python3', '-m', 'sfb.cli',
        '--role', 'alice',
        '--transport', 'icmp',
        '--target', target,
        '--db-log', CLIENT_DB_LOG,
        '--log-profile', 'scp_stalled_icmp_socks',
    ]
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
    recv_exact(sock, addr_len)  # bind addr (ignored)
    recv_exact(sock, 2)  # bind port (ignored)
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
            if content_length is not None and body_bytes >= content_length:
                break
            metrics['bytes'] = body_bytes
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
        percent = (100.0 * total_bytes / expected_total) if expected_total else 0.0
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
            status_bytes[cid] = bval
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
            # Drain remaining updates before exiting.
            while True:
                try:
                    update = progress_q.get_nowait()
                    cid = update.get('client_id')
                    bval = update.get('bytes', 0)
                    status_bytes[cid] = bval
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
    return results, elapsed


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


def main():
    args = parse_args()
    if args.clients < 1:
        raise SystemExit('At least one client is required')

    require_linux_root()
    ensure_logs()

    http_root = os.path.join(ROOT_DIR, 'test_download_files')
    download_path = os.path.join(http_root, args.download_file)
    if not os.path.isfile(download_path):
        raise SystemExit('Download file not found: %s' % download_path)

    http_server = None
    bob = None
    alice = None
    try:
        http_server, _ = start_http_server(http_root, args.http_port)

        bob = start_bob(args.socks_port)
        alice = start_alice(args.target)
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

        results, elapsed = run_downloads(
            args.clients,
            '127.0.0.1',
            args.socks_port,
            '127.0.0.1',
            args.http_port,
            args.download_file,
            remaining,
            os.path.getsize(download_path),
        )
        summary = summarize_results(results, elapsed)

        print('\n=== Download Results ===')
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

        print('\n=== Summary ===')
        print('clients=%d successes=%d failures=%d total_bytes=%d aggregate_rate_mbps=%s avg_client_rate_mbps=%s' % (
            summary['clients'],
            summary['successes'],
            summary['failures'],
            summary['total_bytes'],
            ('%.3f' % summary['aggregate_rate_mbps']) if summary['aggregate_rate_mbps'] is not None else 'n/a',
            ('%.3f' % summary['avg_client_rate_mbps']) if summary['avg_client_rate_mbps'] is not None else 'n/a',
        ))
        print('Logs: server=%s client=%s' % (SERVER_DB_LOG, CLIENT_DB_LOG))
        return 0
    finally:
        shutdown(http_server, bob, alice)


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


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        sys.exit(1)
