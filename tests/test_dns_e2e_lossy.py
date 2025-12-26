# -*- coding: ascii -*-
"""
End-to-end tests with packet loss injection.

Tests file transfer reliability under various network impairment conditions.
Loss is applied only at Alice's transport layer (simulates lossy network path).
"""

from __future__ import absolute_import

import os
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


TEST_PORT = 5353
TEST_DOMAIN = 'lossy.local'


class LossyE2ETest(unittest.TestCase):
    """End-to-end tests with packet loss."""

    @classmethod
    def setUpClass(cls):
        """Create test directories."""
        cls.test_dir = tempfile.mkdtemp(prefix='sfb_lossy_')
        cls.bob_root = os.path.join(cls.test_dir, 'bob')
        cls.alice_root = os.path.join(cls.test_dir, 'alice')
        os.makedirs(cls.bob_root)
        os.makedirs(cls.alice_root)

    @classmethod
    def tearDownClass(cls):
        """Clean up test directories."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        """Set up test fixtures."""
        self.bob_transport = None
        self.bob_tunnel = None
        self.alice_tunnel = None
        self.alice_transport = None
        self.bob_file_module = None
        self.alice_file_module = None
        self.bob_thread = None
        self.alice_runner = None
        self.lossy_alice = None

    def tearDown(self):
        """Clean up after each test."""
        if self.alice_runner:
            self.alice_runner.stop()

        if self.alice_file_module:
            try:
                self.alice_file_module.shutdown()
            except Exception:
                pass

        if self.bob_file_module:
            try:
                self.bob_file_module.shutdown()
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

    def _create_config(self):
        """Create config for tests with fast retransmit for lossy conditions."""
        return Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver='127.0.0.1:%d' % TEST_PORT,
            dns_listen_addr='127.0.0.1:%d' % TEST_PORT,
            tunnel_idle_timeout=600.0,
            tunnel_connect_timeout=30.0,
            tunnel_keepalive_interval=0.1,  # Fast keepalive for quick recovery
            # Fast retransmit for lossy tests
            protocol_initial_rto_ms=100,
            protocol_min_rto_ms=50,
            protocol_max_rto_ms=500,
        )

    def _start_bob(self, config):
        """Start Bob server (no impairment - loss applied at Alice's side)."""
        self.bob_transport = DnsServer(config)
        self.bob_tunnel = BobTunnel(self.bob_transport, config, crypto=Plain())
        self.bob_file_module = FileTransferModule(self.bob_tunnel)

        orig_dir = os.getcwd()

        def serve():
            os.chdir(self.bob_root)
            try:
                self.bob_tunnel.serve_forever()
            finally:
                os.chdir(orig_dir)

        self.bob_thread = threading.Thread(target=serve, daemon=True)
        self.bob_thread.start()
        time.sleep(0.1)

    def _start_alice(self, config, impairment=None):
        """Start Alice client with optional impairment."""
        self.alice_transport = DnsClient(config)

        if impairment:
            self.lossy_alice = LossyTransport(self.alice_transport, impairment)
            transport = self.lossy_alice
        else:
            transport = self.alice_transport

        self.alice_tunnel = AliceTunnel(transport, config, crypto=Plain())
        self.alice_tunnel.connect(timeout=30.0)

        self.alice_runner = _TunnelRunner(self.alice_tunnel)
        self.alice_runner.start()

        self.alice_file_module = FileTransferModule(self.alice_tunnel)

    def _print_stats(self):
        """Print impairment statistics."""
        if self.lossy_alice:
            stats = self.lossy_alice.stats()
            print('\nAlice transport stats:')
            print('  Send: dropped=%d/%d' % (stats['send']['dropped'], stats['send']['sent']))
            print('  Recv: dropped=%d/%d' % (stats['recv']['dropped'], stats['recv']['sent']))

    # --- Tests with 10% loss ---

    def test_connect_10pct_loss(self):
        """Test connection with 10% packet loss."""
        config = self._create_config()
        impairment = NetworkImpairment(loss_rate=0.10, seed=42)

        self._start_bob(config)
        self._start_alice(config, impairment)

        self.assertEqual(self.alice_tunnel._state, TunnelState.CONNECTED)
        self._print_stats()

    def test_get_1kb_10pct_loss(self):
        """Test 1KB download with 10% loss."""
        config = self._create_config()
        impairment = NetworkImpairment(loss_rate=0.10, seed=42)

        test_content = b'X' * 1024
        with open(os.path.join(self.bob_root, 'lossy1kb.bin'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config, impairment)

        local_path = os.path.join(self.alice_root, 'lossy1kb.bin')
        self.alice_file_module.get('lossy1kb.bin', local_path, timeout=60.0)

        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(downloaded, test_content)
        self._print_stats()

    def test_put_1kb_10pct_loss(self):
        """Test 1KB upload with 10% loss."""
        config = self._create_config()
        impairment = NetworkImpairment(loss_rate=0.10, seed=42)

        test_content = b'Y' * 1024
        local_path = os.path.join(self.alice_root, 'upload_lossy1kb.bin')
        with open(local_path, 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config, impairment)

        self.alice_file_module.put(local_path, 'upload_lossy1kb.bin', timeout=60.0)

        remote_path = os.path.join(self.bob_root, 'upload_lossy1kb.bin')
        with open(remote_path, 'rb') as f:
            uploaded = f.read()
        self.assertEqual(uploaded, test_content)
        self._print_stats()

    # --- Tests with 20% loss ---

    def test_get_1kb_20pct_loss(self):
        """Test 1KB download with 20% loss (no artificial delay)."""
        config = self._create_config()
        impairment = NetworkImpairment(loss_rate=0.20, seed=42)

        test_content = b'M' * 1024
        with open(os.path.join(self.bob_root, 'loss20_1kb.bin'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config, impairment)

        local_path = os.path.join(self.alice_root, 'loss20_1kb.bin')
        self.alice_file_module.get('loss20_1kb.bin', local_path, timeout=90.0)

        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(downloaded, test_content)
        self._print_stats()


class _TunnelRunner(object):
    """Runs tunnel tick loop in background thread."""

    def __init__(self, tunnel):
        self._tunnel = tunnel
        self._stop = False
        self._thread = None

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
            time.sleep(0.001)


if __name__ == '__main__':
    unittest.main()
