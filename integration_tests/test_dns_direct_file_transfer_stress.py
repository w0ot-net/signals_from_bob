# -*- coding: ascii -*-
"""Integration tests for DNS direct mode file transfer stress."""

from __future__ import absolute_import

import hashlib
import os
import shutil
import socket
import tempfile
import threading
import unittest

try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty

from sfb import time_provider
from sfb.config import Config
from sfb.crypto import Plain
from sfb.modules.file_transfer import FileTransferModule
from sfb.transport.dns import DnsClient, DnsServer
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState


TEST_PORT = 5353
TEST_DOMAIN = 'test.local'
TRANSFER_TIMEOUT = 300.0
TRANSFER_MULTIPLIER = 2
CONCURRENCY_CASES = [
    (1, 1),
    (2, 2),
    (4, 4),
    (1, 4),
    (4, 1),
    (32, 32),
    (64, 64),
    (1, 32),
    (32, 1),
]


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _source_path():
    return os.path.join(_repo_root(), 'test_download_files', '0_1MB.bin')


def _ensure_dir(path):
    if os.path.isdir(path):
        return
    os.makedirs(path)


def _port_available(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except socket.error:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _sha256(path):
    digest = hashlib.sha256()
    handle = open(path, 'rb')
    try:
        while True:
            data = handle.read(1024 * 128)
            if not data:
                break
            digest.update(data)
    finally:
        handle.close()
    return digest.hexdigest()


def _start_put_workers(module, local_path, remote_paths, concurrency, timeout):
    work = Queue()
    for remote_path in remote_paths:
        work.put(remote_path)
    errors = []
    errors_lock = threading.Lock()

    def worker():
        while True:
            try:
                remote_path = work.get_nowait()
            except Empty:
                return
            try:
                module.put(local_path, remote_path, timeout=timeout)
            except Exception as exc:
                with errors_lock:
                    errors.append(exc)
            finally:
                work.task_done()

    threads = []
    for _ in range(concurrency):
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        threads.append(thread)
    return work, threads, errors


class _TunnelRunner(object):
    """Runs tunnel tick loop in background thread."""

    def __init__(self, tunnel):
        self._tunnel = tunnel
        self._stop = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop and self._tunnel._state == TunnelState.CONNECTED:
            try:
                self._tunnel.tick()
            except Exception:
                pass
            time_provider.sleep(0.001)


class DnsDirectFileTransferStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._port_ready = _port_available('127.0.0.1', TEST_PORT)
        cls._source_path = _source_path()
        cls._source_ok = os.path.isfile(cls._source_path)
        cls._source_size = None
        cls._source_hash = None
        if cls._source_ok:
            cls._source_size = os.path.getsize(cls._source_path)
            cls._source_hash = _sha256(cls._source_path)
        cls._tmp_dir = tempfile.mkdtemp(prefix='sfb_dns_direct_stress_')
        cls._alice_root = os.path.join(cls._tmp_dir, 'alice')
        cls._bob_root = os.path.join(cls._tmp_dir, 'bob')
        _ensure_dir(cls._alice_root)
        _ensure_dir(cls._bob_root)
        cls._alice_source = os.path.join(cls._alice_root, '0_1MB.bin')
        cls._bob_source = os.path.join(cls._bob_root, '0_1MB.bin')
        if cls._source_ok:
            shutil.copyfile(cls._source_path, cls._alice_source)
            shutil.copyfile(cls._source_path, cls._bob_source)
        cls._max_active = max([a + b for (a, b) in CONCURRENCY_CASES])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def setUp(self):
        if not self._port_ready:
            self.skipTest('port 5353 unavailable (DNS direct mode)')
        if not self._source_ok:
            self.skipTest('missing test_download_files/1MB.bin')
        self._bob_transport = None
        self._bob_tunnel = None
        self._alice_tunnel = None
        self._bob_file = None
        self._alice_file = None
        self._bob_thread = None
        self._alice_runner = None

    def tearDown(self):
        if self._alice_runner:
            self._alice_runner.stop()
        if self._alice_file:
            try:
                self._alice_file.shutdown()
            except Exception:
                pass
        if self._bob_file:
            try:
                self._bob_file.shutdown()
            except Exception:
                pass
        if self._alice_tunnel:
            try:
                self._alice_tunnel.close()
            except Exception:
                pass
        if self._bob_tunnel:
            try:
                self._bob_tunnel.close()
            except Exception:
                pass
        if self._bob_transport:
            try:
                self._bob_transport.close()
            except Exception:
                pass
        if self._bob_thread and self._bob_thread.is_alive():
            self._bob_thread.join(timeout=2.0)
        time_provider.sleep(0.1)

    def _create_config(self):
        config = Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver='127.0.0.1:%d' % TEST_PORT,
            dns_listen_addr='127.0.0.1:%d' % TEST_PORT,
            tunnel_idle_timeout=600.0,
            tunnel_no_response_timeout=600.0,
            tunnel_connect_timeout=10.0,
            tunnel_keepalive_interval=1.0,
        )
        config.file_transfer_max_active = self._max_active
        return config

    def _start_bob(self, config):
        self._bob_transport = DnsServer(config)
        self._bob_tunnel = BobTunnel(self._bob_transport, config, crypto=Plain())
        self._bob_file = FileTransferModule(self._bob_tunnel)
        self._bob_tunnel.allow_message_type(FileTransferModule.TYPE)

        def serve():
            self._bob_tunnel.serve_forever()

        self._bob_thread = threading.Thread(target=serve)
        self._bob_thread.daemon = True
        self._bob_thread.start()
        time_provider.sleep(0.1)

    def _start_alice(self, config):
        transport = DnsClient(config)
        self._alice_tunnel = AliceTunnel(transport, config, crypto=Plain())
        self._alice_tunnel.connect(timeout=config.tunnel_connect_timeout)
        self._alice_runner = _TunnelRunner(self._alice_tunnel)
        self._alice_runner.start()
        self._alice_file = FileTransferModule(self._alice_tunnel)

    def _verify_files(self, paths, expected_size, expected_hash=None):
        for path in paths:
            self.assertTrue(os.path.isfile(path))
            size = os.path.getsize(path)
            self.assertEqual(size, expected_size)
        if paths and expected_hash is not None:
            self.assertEqual(_sha256(paths[0]), expected_hash)

    def _cleanup_files(self, paths):
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_bidirectional_file_transfer_stress(self):
        config = self._create_config()
        self._start_bob(config)
        self._start_alice(config)
        self.assertEqual(self._alice_tunnel._state, TunnelState.CONNECTED)

        for case_index, (alice_conc, bob_conc) in enumerate(CONCURRENCY_CASES):
            alice_count = max(1, alice_conc * TRANSFER_MULTIPLIER)
            bob_count = max(1, bob_conc * TRANSFER_MULTIPLIER)
            alice_remote = []
            bob_remote = []
            for index in range(alice_count):
                alice_remote.append(os.path.join(
                    self._bob_root,
                    'alice_put_%d_%d.bin' % (case_index, index),
                ))
            for index in range(bob_count):
                bob_remote.append(os.path.join(
                    self._alice_root,
                    'bob_put_%d_%d.bin' % (case_index, index),
                ))

            alice_work, alice_threads, alice_errors = _start_put_workers(
                self._alice_file,
                self._alice_source,
                alice_remote,
                alice_conc,
                TRANSFER_TIMEOUT,
            )
            bob_work, bob_threads, bob_errors = _start_put_workers(
                self._bob_file,
                self._bob_source,
                bob_remote,
                bob_conc,
                TRANSFER_TIMEOUT,
            )
            try:
                alice_work.join()
                bob_work.join()
                for thread in alice_threads + bob_threads:
                    thread.join()
                if alice_errors:
                    raise alice_errors[0]
                if bob_errors:
                    raise bob_errors[0]
                self._verify_files(
                    alice_remote,
                    self._source_size,
                    expected_hash=self._source_hash,
                )
                self._verify_files(
                    bob_remote,
                    self._source_size,
                    expected_hash=self._source_hash,
                )
            finally:
                self._cleanup_files(alice_remote)
                self._cleanup_files(bob_remote)


if __name__ == '__main__':
    unittest.main()
