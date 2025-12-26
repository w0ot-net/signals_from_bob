# -*- coding: ascii -*-
"""
End-to-end tests using real DNS transport.

Tests file transfer between Alice and Bob using direct DNS
(Alice connects directly to Bob's DNS server on port 5353).
"""

from __future__ import absolute_import

import os
import shutil
import tempfile
import threading
import time
import unittest

from sfb.config import Config
from sfb.crypto import Plain, XOR
from sfb.transport.dns import DnsClient, DnsServer
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState
from sfb.modules.file_transfer import FileTransferModule


TEST_PORT = 5353
TEST_DOMAIN = 'test.local'


class DnsE2ETest(unittest.TestCase):
    """End-to-end tests with real DNS transport."""

    @classmethod
    def setUpClass(cls):
        """Create test directories."""
        cls.test_dir = tempfile.mkdtemp(prefix='sfb_test_')
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
        self.bob_file_module = None
        self.alice_file_module = None
        self.bob_thread = None
        self.alice_runner = None

    def tearDown(self):
        """Clean up after each test."""
        # Stop Alice runner
        if self.alice_runner:
            self.alice_runner.stop()

        # Shutdown modules
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

        # Close tunnels
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

        # Close transport explicitly
        if self.bob_transport:
            try:
                self.bob_transport.close()
            except Exception:
                pass

        # Wait for Bob thread
        if self.bob_thread and self.bob_thread.is_alive():
            self.bob_thread.join(timeout=2.0)

        # Allow socket to fully release
        time.sleep(0.1)

    def _create_config(self):
        """Create config for tests."""
        return Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver='127.0.0.1:%d' % TEST_PORT,
            dns_listen_addr='127.0.0.1:%d' % TEST_PORT,
            tunnel_idle_timeout=600.0,
            tunnel_connect_timeout=10.0,
            tunnel_keepalive_interval=1.0,
        )

    def _start_bob(self, config, crypto=None):
        """Start Bob server in background thread."""
        if crypto is None:
            crypto = Plain()

        self.bob_transport = DnsServer(config)
        self.bob_tunnel = BobTunnel(self.bob_transport, config, crypto=crypto)
        self.bob_file_module = FileTransferModule(self.bob_tunnel)

        # Change to bob root for file operations
        orig_dir = os.getcwd()

        def serve():
            os.chdir(self.bob_root)
            try:
                self.bob_tunnel.serve_forever()
            finally:
                os.chdir(orig_dir)

        self.bob_thread = threading.Thread(target=serve, daemon=True)
        self.bob_thread.start()

        # Give server time to start
        time.sleep(0.1)

    def _start_alice(self, config, crypto=None):
        """Start Alice client and connect."""
        if crypto is None:
            crypto = Plain()

        transport = DnsClient(config)
        self.alice_tunnel = AliceTunnel(transport, config, crypto=crypto)

        # Connect
        self.alice_tunnel.connect(timeout=10.0)

        # Start background tick loop
        self.alice_runner = _TunnelRunner(self.alice_tunnel)
        self.alice_runner.start()

        # Create file module
        self.alice_file_module = FileTransferModule(self.alice_tunnel)

    def test_connect(self):
        """Test basic connection."""
        config = self._create_config()

        self._start_bob(config)
        self._start_alice(config)

        self.assertEqual(self.alice_tunnel._state, TunnelState.CONNECTED)

    def test_list_empty_dir(self):
        """Test listing empty directory."""
        config = self._create_config()

        # Create empty subdir
        empty_dir = os.path.join(self.bob_root, 'empty')
        os.makedirs(empty_dir, exist_ok=True)

        self._start_bob(config)
        self._start_alice(config)

        result = self.alice_file_module.list_dir('empty', timeout=30.0)
        self.assertEqual(result, [])

    def test_list_dir_with_files(self):
        """Test listing directory with files."""
        config = self._create_config()

        # Create test files
        test_subdir = os.path.join(self.bob_root, 'files')
        os.makedirs(test_subdir, exist_ok=True)

        with open(os.path.join(test_subdir, 'a.txt'), 'w') as f:
            f.write('hello')
        with open(os.path.join(test_subdir, 'b.txt'), 'w') as f:
            f.write('world!')
        os.makedirs(os.path.join(test_subdir, 'subdir'), exist_ok=True)

        self._start_bob(config)
        self._start_alice(config)

        result = self.alice_file_module.list_dir('files', timeout=30.0)

        # Sort by name for consistent comparison
        result = sorted(result, key=lambda x: x['name'])

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['name'], 'a.txt')
        self.assertEqual(result[0]['size'], 5)
        self.assertFalse(result[0]['dir'])

        self.assertEqual(result[1]['name'], 'b.txt')
        self.assertEqual(result[1]['size'], 6)
        self.assertFalse(result[1]['dir'])

        self.assertEqual(result[2]['name'], 'subdir')
        self.assertTrue(result[2]['dir'])

    def test_get_file(self):
        """Test downloading a file."""
        config = self._create_config()

        # Create test file on Bob's side
        test_content = b'Hello from Bob! This is test content.'
        with open(os.path.join(self.bob_root, 'download.txt'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config)

        # Download to Alice's side
        local_path = os.path.join(self.alice_root, 'downloaded.txt')
        self.alice_file_module.get('download.txt', local_path, timeout=30.0)

        # Verify content
        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(downloaded, test_content)

    def test_put_file(self):
        """Test uploading a file."""
        config = self._create_config()

        # Create test file on Alice's side
        test_content = b'Hello from Alice! Uploading this.'
        local_path = os.path.join(self.alice_root, 'upload.txt')
        with open(local_path, 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config)

        # Upload to Bob's side
        self.alice_file_module.put(local_path, 'uploaded.txt', timeout=30.0)

        # Verify content on Bob's side
        remote_path = os.path.join(self.bob_root, 'uploaded.txt')
        with open(remote_path, 'rb') as f:
            uploaded = f.read()
        self.assertEqual(uploaded, test_content)

    def test_get_1kb_file(self):
        """Test downloading a 1KB file (multi-packet)."""
        config = self._create_config()

        test_content = b'X' * 1024
        with open(os.path.join(self.bob_root, '1kb.bin'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config)

        local_path = os.path.join(self.alice_root, '1kb.bin')
        self.alice_file_module.get('1kb.bin', local_path, timeout=None)

        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(len(downloaded), len(test_content))
        self.assertEqual(downloaded, test_content)

    def test_put_1kb_file(self):
        """Test uploading a 1KB file (multi-packet)."""
        config = self._create_config()

        test_content = b'Y' * 1024
        local_path = os.path.join(self.alice_root, '1kb_up.bin')
        with open(local_path, 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config)

        self.alice_file_module.put(local_path, '1kb_up.bin', timeout=None)

        remote_path = os.path.join(self.bob_root, '1kb_up.bin')
        with open(remote_path, 'rb') as f:
            uploaded = f.read()
        self.assertEqual(len(uploaded), len(test_content))
        self.assertEqual(uploaded, test_content)

    def test_get_1mb_file(self):
        """Test downloading a 1MB file. This test may take several minutes."""
        config = self._create_config()

        # 1MB file
        test_content = b'A' * (1024 * 1024)
        with open(os.path.join(self.bob_root, '1mb.bin'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config)
        self._start_alice(config)

        local_path = os.path.join(self.alice_root, '1mb.bin')
        # No timeout - let it take as long as needed
        self.alice_file_module.get('1mb.bin', local_path, timeout=None)

        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(len(downloaded), len(test_content))
        self.assertEqual(downloaded, test_content)

    def test_with_encryption(self):
        """Test file transfer with XOR encryption."""
        config = self._create_config()
        psk = b'test_secret_key'

        # Create test file
        test_content = b'Encrypted content here!'
        with open(os.path.join(self.bob_root, 'secret.txt'), 'wb') as f:
            f.write(test_content)

        self._start_bob(config, crypto=XOR(psk))
        self._start_alice(config, crypto=XOR(psk))

        # Download
        local_path = os.path.join(self.alice_root, 'secret.txt')
        self.alice_file_module.get('secret.txt', local_path, timeout=30.0)

        # Verify
        with open(local_path, 'rb') as f:
            downloaded = f.read()
        self.assertEqual(downloaded, test_content)


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
