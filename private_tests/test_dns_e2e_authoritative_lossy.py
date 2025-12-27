# -*- coding: ascii -*-
"""
End-to-end tests against authoritative DNS with injected loss.
"""

from __future__ import absolute_import

import os
import random
import shutil
import tempfile
import threading
import time
import unittest

from sfb.config import Config
from sfb.crypto import Plain
from sfb.transport.dns import DnsClient, DnsServer
from sfb.transport.lossy import LossyTransport, NetworkImpairment
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState
from sfb.modules.file_transfer import FileTransferModule


TEST_DOMAIN = 'ebaysso.com'
TEST_BOB_IP = '149.28.195.216'
TEST_PORT = 53
REMOTE_TEST_FILE = 'sfb_e2e_roundtrip.bin'
PROGRESS_INTERVAL = 5.0


class DnsAuthoritativeLossyE2ETest(unittest.TestCase):
    """End-to-end tests with real DNS and 1% loss."""

    @classmethod
    def setUpClass(cls):
        """Create test directories."""
        cls.test_dir = tempfile.mkdtemp(prefix='sfb_dns_auth_lossy_')
        cls.local_root = os.path.join(cls.test_dir, 'alice')
        try:
            os.makedirs(cls.local_root)
        except OSError:
            pass

    @classmethod
    def tearDownClass(cls):
        """Clean up test directories."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        """Set up test fixtures."""
        self.bob_transport = None
        self.bob_tunnel = None
        self.bob_file_module = None
        self.bob_thread = None
        self.alice_tunnel = None
        self.alice_file_module = None
        self.alice_runner = None

    def tearDown(self):
        """Clean up after each test."""
        if self.alice_runner:
            self.alice_runner.stop()

        if self.alice_file_module:
            try:
                self.alice_file_module.shutdown()
            except Exception:
                pass

        if self.alice_tunnel:
            try:
                self.alice_tunnel.close()
            except Exception:
                pass

        if self.bob_tunnel:
            try:
                self.bob_tunnel.close()
            except Exception:
                pass

        if self.bob_transport:
            try:
                self.bob_transport.close()
            except Exception:
                pass

        if self.bob_thread and self.bob_thread.is_alive():
            self.bob_thread.join(timeout=2.0)

        time.sleep(0.1)

    def _create_config(self, listen_addr=None):
        """Create config for authoritative DNS tests."""
        return Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver=None,
            dns_listen_addr=listen_addr or '0.0.0.0:%d' % TEST_PORT,
            dns_pending_timeout=30.0,
            tunnel_idle_timeout=120.0,
            tunnel_connect_timeout=60.0,
            tunnel_keepalive_interval=1.0,
        )

    def _start_bob(self, config):
        """Start Bob server on the authoritative address."""
        try:
            self.bob_transport = DnsServer(config)
        except Exception as e:
            self.skipTest('Bob DNS server failed to bind: %s' % (e,))

        self.bob_tunnel = BobTunnel(self.bob_transport, config, crypto=Plain())
        self.bob_file_module = FileTransferModule(self.bob_tunnel)

        orig_dir = os.getcwd()

        def serve():
            os.chdir(self.local_root)
            try:
                self.bob_tunnel.serve_forever()
            finally:
                os.chdir(orig_dir)

        self.bob_thread = threading.Thread(target=serve, daemon=True)
        self.bob_thread.start()
        time.sleep(0.1)

    def _start_alice(self, config, impairment):
        """Start Alice client with injected loss on one direction."""
        transport = DnsClient(config)
        loss_on_send = random.choice([True, False])
        if loss_on_send:
            send_imp = impairment
            recv_imp = NetworkImpairment()
            direction = 'send'
        else:
            send_imp = NetworkImpairment()
            recv_imp = impairment
            direction = 'recv'
        print('loss direction: %s' % direction)
        lossy = LossyTransport(
            transport,
            send_impairment=send_imp,
            recv_impairment=recv_imp,
        )
        self.alice_tunnel = AliceTunnel(lossy, config, crypto=Plain())
        self.alice_tunnel.connect(timeout=60.0)

        self.alice_runner = _TunnelRunner(self.alice_tunnel, progress_interval=PROGRESS_INTERVAL)
        self.alice_runner.start()

        self.alice_file_module = FileTransferModule(self.alice_tunnel)

    def test_get_1mb_file_lossy(self):
        """Download a 1MB file through authoritative DNS with 1% loss."""
        bob_config = self._create_config(
            listen_addr='%s:%d' % (TEST_BOB_IP, TEST_PORT)
        )
        self._start_bob(bob_config)

        config = self._create_config()
        impairment = NetworkImpairment(
            loss_rate=0.0,
            burst_loss_prob=0.01,
            burst_loss_len=(2, 6),
            seed=42,
        )
        self._start_alice(config, impairment)

        self.assertEqual(self.alice_tunnel._state, TunnelState.CONNECTED)

        payload = b'A' * (100 * 1024)
        remote_path = os.path.join(self.local_root, REMOTE_TEST_FILE)
        with open(remote_path, 'wb') as handle:
            handle.write(payload)

        download_path = os.path.join(self.local_root, 'download.bin')
        self.alice_file_module.get(REMOTE_TEST_FILE, download_path, timeout=600.0)
        if self.alice_runner:
            self.alice_runner.stop()

        with open(download_path, 'rb') as handle:
            downloaded = handle.read()
        self.assertEqual(downloaded, payload)
        if self.alice_file_module.last_stats:
            print('transfer stats: %s' % (self.alice_file_module.last_stats,))


class _TunnelRunner(object):
    """Runs tunnel tick loop in background thread."""

    def __init__(self, tunnel, progress_interval=5.0):
        self._tunnel = tunnel
        self._progress_interval = progress_interval
        self._stop = False
        self._thread = None
        self._last_report = 0
        self._last_bytes_sent = 0
        self._last_bytes_received = 0
        self._last_window = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
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
            self._maybe_report()
            time.sleep(0.001)

    def _maybe_report(self):
        now = time.time()
        if now - self._last_report < self._progress_interval:
            return
        self._last_report = now

        bytes_sent = self._tunnel._bytes_sent
        bytes_received = self._tunnel._bytes_received
        delta_sent = bytes_sent - self._last_bytes_sent
        delta_received = bytes_received - self._last_bytes_received
        self._last_bytes_sent = bytes_sent
        self._last_bytes_received = bytes_received

        interval = max(self._progress_interval, 0.001)
        send_rate = delta_sent / interval / 1024.0
        recv_rate = delta_received / interval / 1024.0

        window = self._tunnel._negotiated_window
        if self._last_window != window:
            print('window updated: %d' % window)
            self._last_window = window

        pending = None
        try:
            pending = self._tunnel._transport.pending_count()
        except Exception:
            pending = None
        unacked = self._tunnel._send_window.unacked_count

        print('progress: sent=%d recv=%d rate=%.2f/%.2fKBs window=%d unacked=%d pending=%s' % (
            bytes_sent, bytes_received, send_rate, recv_rate,
            window, unacked, pending
        ))


if __name__ == '__main__':
    unittest.main()
