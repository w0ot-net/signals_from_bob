# -*- coding: ascii -*-
"""Integration tests for in-memory file transfer stress."""

from __future__ import absolute_import

import os
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
from sfb.transport import create_inmemory_transport_pair
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState


_TRANSFER_COUNT = 10000
_CONCURRENCY = 20
_DUMMY_SIZE = 4096


def _make_config():
    config = Config()
    config.tunnel_connect_timeout = 5.0
    config.tunnel_no_response_timeout = 120.0
    config.tunnel_idle_timeout = 120.0
    config.tunnel_bob_poll_interval = 0.02
    config.tunnel_bob_poll_interval_bg = 0.005
    config.tunnel_tick_sleep = 0.0005
    config.max_in_flight = 64
    config.tunnel_initial_window = 32
    config.channel_id_reuse_cooldown = 0.0
    config.file_transfer_max_active = _CONCURRENCY
    return config


def _write_dummy_file(path, size):
    handle = open(path, 'wb')
    try:
        handle.write(b'A' * size)
    finally:
        handle.close()


def _use_devnull():
    return os.name != 'nt' and os.path.isabs(os.devnull)


def _cleanup_sink_files(tmp_dir, prefix):
    if not os.path.isdir(tmp_dir):
        return
    for name in os.listdir(tmp_dir):
        if name.startswith(prefix):
            try:
                os.unlink(os.path.join(tmp_dir, name))
            except OSError:
                pass


def _run_transfer_batch(module, source_path, tmp_dir, sink_prefix,
                        use_devnull, count, concurrency, timeout):
    work = Queue()
    for index in range(count):
        work.put(index)
    errors = []
    errors_lock = threading.Lock()

    def worker(worker_id):
        while True:
            try:
                index = work.get_nowait()
            except Empty:
                return
            if use_devnull:
                dest_path = os.devnull
            else:
                dest_path = os.path.join(
                    tmp_dir,
                    '%s%d_%d.bin' % (sink_prefix, worker_id, index),
                )
            try:
                module.get(source_path, dest_path, timeout=timeout)
            except Exception as exc:
                with errors_lock:
                    errors.append(exc)
            finally:
                if not use_devnull:
                    try:
                        os.unlink(dest_path)
                    except OSError:
                        pass
                work.task_done()

    threads = []
    for worker_id in range(concurrency):
        t = threading.Thread(target=worker, args=(worker_id,))
        t.daemon = True
        threads.append(t)
        t.start()
    work.join()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


class InMemoryFileTransferStressTests(unittest.TestCase):
    def setUp(self):
        self._config = _make_config()
        self._tmp_dir = tempfile.gettempdir()
        self._dummy_path = os.path.join(
            self._tmp_dir,
            'sfb_file_transfer_dummy_%d.bin' % os.getpid(),
        )
        self._use_devnull = _use_devnull()
        self._sink_prefix = 'sfb_file_transfer_sink_%d_' % os.getpid()

        if os.path.exists(self._dummy_path):
            os.unlink(self._dummy_path)
        if not self._use_devnull:
            _cleanup_sink_files(self._tmp_dir, self._sink_prefix)
        _write_dummy_file(self._dummy_path, _DUMMY_SIZE)

        client, server = create_inmemory_transport_pair(
            self._config,
            send_mtu=4096,
            recv_mtu=4096,
        )
        self._alice = AliceTunnel(client, self._config, crypto=Plain())
        self._bob = BobTunnel(server, self._config, crypto=Plain())

        self._alice_file = FileTransferModule(self._alice)
        self._bob_file = FileTransferModule(self._bob)
        self._bob.allow_message_type(FileTransferModule.TYPE)

        self._bob.start_background()
        self._alice.connect(timeout=self._config.tunnel_connect_timeout)
        self.assertEqual(self._alice.state, TunnelState.CONNECTED)
        self._alice.start_background()
        time_provider.sleep(0.05)

    def tearDown(self):
        if self._alice_file is not None:
            self._alice_file.shutdown()
        if self._bob_file is not None:
            self._bob_file.shutdown()
        if self._alice is not None:
            self._alice.stop_background()
            self._alice.close()
        if self._bob is not None:
            self._bob.stop_background()
            self._bob.close()
        if not self._use_devnull:
            _cleanup_sink_files(self._tmp_dir, self._sink_prefix)
        if self._dummy_path and os.path.exists(self._dummy_path):
            os.unlink(self._dummy_path)

    def test_bidirectional_file_transfer_stress(self):
        timeout = 5.0
        _run_transfer_batch(
            self._alice_file,
            self._dummy_path,
            self._tmp_dir,
            self._sink_prefix,
            self._use_devnull,
            _TRANSFER_COUNT,
            _CONCURRENCY,
            timeout,
        )
        _run_transfer_batch(
            self._bob_file,
            self._dummy_path,
            self._tmp_dir,
            self._sink_prefix,
            self._use_devnull,
            _TRANSFER_COUNT,
            _CONCURRENCY,
            timeout,
        )


if __name__ == '__main__':
    unittest.main()
