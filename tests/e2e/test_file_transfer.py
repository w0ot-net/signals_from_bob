# -*- coding: ascii -*-
"""End-to-end tests for file transfer module."""

from __future__ import absolute_import

import os
import shutil
import tempfile
import threading
import time
import unittest

from sfb.tunnel import AliceTunnel, BobTunnel
from sfb.crypto import Plain
from sfb.modules.file_transfer import FileTransferModule, FileTransferError

# Import PairedTransport and make_test_config from test_tunnel
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from test_tunnel import PairedTransport, make_test_config


class FileTransferTestCase(unittest.TestCase):
    """Base class for file transfer tests with tunnel setup/teardown."""

    def setUp(self):
        """Set up paired tunnels and file transfer modules."""
        # Create temp directories for testing
        self.test_dir = tempfile.mkdtemp(prefix='sfb_test_')
        self.alice_dir = os.path.join(self.test_dir, 'alice')
        self.bob_dir = os.path.join(self.test_dir, 'bob')
        os.makedirs(self.alice_dir)
        os.makedirs(self.bob_dir)

        # Create paired transport
        self.pair = PairedTransport()
        self.config = make_test_config()

        # Create tunnels
        self.alice_tunnel = AliceTunnel(
            self.pair.make_alice_transport(),
            self.config,
            crypto=Plain(),
        )
        self.bob_tunnel = BobTunnel(
            self.pair.make_bob_server(),
            self.config,
            crypto=Plain(),
        )

        # Create file transfer modules
        self.alice_ft = FileTransferModule(self.alice_tunnel)
        self.bob_ft = FileTransferModule(self.bob_tunnel)

        # Start Bob in background thread
        self.bob_stop = threading.Event()
        self.bob_thread = threading.Thread(target=self._run_bob)
        self.bob_thread.daemon = True
        self.bob_thread.start()

        # Connect Alice
        self.alice_tunnel.connect(timeout=5.0)

        # Run a few ticks to complete negotiation
        for _ in range(5):
            self.alice_tunnel.tick()
            time.sleep(0.01)

    def tearDown(self):
        """Clean up tunnels and temp files."""
        # Stop Bob
        self.bob_stop.set()
        self.bob_tunnel.close()
        self.bob_thread.join(timeout=2.0)

        # Close Alice
        self.alice_tunnel.close()

        # Shutdown modules
        self.alice_ft.shutdown()
        self.bob_ft.shutdown()

        # Clean up temp directory
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _run_bob(self):
        """Run Bob's serve loop until stopped."""
        while not self.bob_stop.is_set():
            try:
                data, responder = self.bob_tunnel._transport.recv(timeout=0.1)
                if data is not None:
                    self.bob_tunnel.handle_request(data, responder)
            except Exception:
                if not self.bob_stop.is_set():
                    raise

    def _tick_alice(self, count=10, delay=0.02):
        """Run Alice's tick loop."""
        for _ in range(count):
            self.alice_tunnel.tick()
            time.sleep(delay)

    def _create_test_file(self, directory, name, content):
        """Create a test file with given content."""
        path = os.path.join(directory, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def _read_file(self, path):
        """Read file contents."""
        with open(path, 'rb') as f:
            return f.read()


class TestListDir(FileTransferTestCase):
    """Tests for list_dir operation."""

    def test_list_empty_directory(self):
        """List an empty directory."""
        # Create empty subdir in Bob's space
        empty_dir = os.path.join(self.bob_dir, 'empty')
        os.makedirs(empty_dir)

        # Alice lists Bob's directory (run in thread since it blocks)
        result = [None]
        error = [None]

        def do_list():
            try:
                result[0] = self.alice_ft.list_dir(empty_dir, timeout=5.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_list)
        t.start()

        # Tick Alice while waiting
        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=1.0)
        self.assertIsNone(error[0], 'list_dir raised: %s' % error[0])
        self.assertEqual(result[0], [])

    def test_list_directory_with_files(self):
        """List a directory containing files."""
        # Create files in Bob's space
        self._create_test_file(self.bob_dir, 'file1.txt', b'hello')
        self._create_test_file(self.bob_dir, 'file2.bin', b'world!')
        subdir = os.path.join(self.bob_dir, 'subdir')
        os.makedirs(subdir)

        result = [None]
        error = [None]

        def do_list():
            try:
                result[0] = self.alice_ft.list_dir(self.bob_dir, timeout=5.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_list)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=1.0)
        self.assertIsNone(error[0], 'list_dir raised: %s' % error[0])

        # Check results
        files = {f['name']: f for f in result[0]}
        self.assertIn('file1.txt', files)
        self.assertIn('file2.bin', files)
        self.assertIn('subdir', files)
        self.assertEqual(files['file1.txt']['size'], 5)
        self.assertEqual(files['file2.bin']['size'], 6)
        self.assertFalse(files['file1.txt']['dir'])
        self.assertTrue(files['subdir']['dir'])

    def test_list_nonexistent_directory(self):
        """List a directory that doesn't exist."""
        result = [None]
        error = [None]

        def do_list():
            try:
                result[0] = self.alice_ft.list_dir('/nonexistent/path', timeout=5.0)
            except FileTransferError as e:
                error[0] = e

        t = threading.Thread(target=do_list)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=1.0)
        self.assertIsNotNone(error[0])
        self.assertEqual(error[0].code, 'not_found')


class TestGetFile(FileTransferTestCase):
    """Tests for get (download) operation."""

    def test_get_small_file(self):
        """Download a small file."""
        # Create file on Bob's side
        content = b'Hello, this is a test file!'
        src_path = self._create_test_file(self.bob_dir, 'test.txt', content)
        dst_path = os.path.join(self.alice_dir, 'received.txt')

        error = [None]

        def do_get():
            try:
                self.alice_ft.get(src_path, dst_path, timeout=10.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_get)
        t.start()

        for _ in range(100):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'get raised: %s' % error[0])

        # Verify file was received correctly
        self.assertTrue(os.path.exists(dst_path))
        received = self._read_file(dst_path)
        self.assertEqual(received, content)

    def test_get_larger_file(self):
        """Download a file larger than chunk size."""
        # Create 50KB file (larger than default 8KB chunk)
        content = b'X' * 50000
        src_path = self._create_test_file(self.bob_dir, 'large.bin', content)
        dst_path = os.path.join(self.alice_dir, 'large_received.bin')

        error = [None]

        def do_get():
            try:
                self.alice_ft.get(src_path, dst_path, timeout=30.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_get)
        t.start()

        for _ in range(500):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=5.0)
        self.assertIsNone(error[0], 'get raised: %s' % error[0])

        # Verify
        self.assertTrue(os.path.exists(dst_path))
        received = self._read_file(dst_path)
        self.assertEqual(len(received), len(content))
        self.assertEqual(received, content)

    def test_get_nonexistent_file(self):
        """Download a file that doesn't exist."""
        dst_path = os.path.join(self.alice_dir, 'wont_exist.txt')
        error = [None]

        def do_get():
            try:
                self.alice_ft.get('/nonexistent/file.txt', dst_path, timeout=5.0)
            except FileTransferError as e:
                error[0] = e

        t = threading.Thread(target=do_get)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNotNone(error[0])
        self.assertEqual(error[0].code, 'not_found')
        self.assertFalse(os.path.exists(dst_path))

    def test_get_empty_file(self):
        """Download an empty file."""
        src_path = self._create_test_file(self.bob_dir, 'empty.txt', b'')
        dst_path = os.path.join(self.alice_dir, 'empty_received.txt')

        error = [None]

        def do_get():
            try:
                self.alice_ft.get(src_path, dst_path, timeout=5.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_get)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'get raised: %s' % error[0])
        self.assertTrue(os.path.exists(dst_path))
        self.assertEqual(self._read_file(dst_path), b'')


class TestPutFile(FileTransferTestCase):
    """Tests for put (upload) operation."""

    def test_put_small_file(self):
        """Upload a small file."""
        # Create file on Alice's side
        content = b'This file is being uploaded!'
        src_path = self._create_test_file(self.alice_dir, 'upload.txt', content)
        dst_path = os.path.join(self.bob_dir, 'uploaded.txt')

        error = [None]

        def do_put():
            try:
                self.alice_ft.put(src_path, dst_path, timeout=10.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_put)
        t.start()

        for _ in range(100):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'put raised: %s' % error[0])

        # Verify file was uploaded correctly
        self.assertTrue(os.path.exists(dst_path))
        uploaded = self._read_file(dst_path)
        self.assertEqual(uploaded, content)

    def test_put_larger_file(self):
        """Upload a file larger than chunk size."""
        # Create 50KB file
        content = b'Y' * 50000
        src_path = self._create_test_file(self.alice_dir, 'large_upload.bin', content)
        dst_path = os.path.join(self.bob_dir, 'large_uploaded.bin')

        error = [None]

        def do_put():
            try:
                self.alice_ft.put(src_path, dst_path, timeout=30.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_put)
        t.start()

        for _ in range(500):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=5.0)
        self.assertIsNone(error[0], 'put raised: %s' % error[0])

        # Verify
        self.assertTrue(os.path.exists(dst_path))
        uploaded = self._read_file(dst_path)
        self.assertEqual(len(uploaded), len(content))
        self.assertEqual(uploaded, content)

    def test_put_nonexistent_source(self):
        """Upload a file that doesn't exist locally."""
        error = [None]

        def do_put():
            try:
                self.alice_ft.put(
                    '/nonexistent/source.txt',
                    os.path.join(self.bob_dir, 'wont_exist.txt'),
                    timeout=5.0
                )
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_put)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        # Should raise an OS error (file not found) before even sending
        self.assertIsNotNone(error[0])

    def test_put_empty_file(self):
        """Upload an empty file."""
        src_path = self._create_test_file(self.alice_dir, 'empty_upload.txt', b'')
        dst_path = os.path.join(self.bob_dir, 'empty_uploaded.txt')

        error = [None]

        def do_put():
            try:
                self.alice_ft.put(src_path, dst_path, timeout=5.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_put)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'put raised: %s' % error[0])
        self.assertTrue(os.path.exists(dst_path))
        self.assertEqual(self._read_file(dst_path), b'')


class TestBidirectional(FileTransferTestCase):
    """Tests for bidirectional file transfer."""

    def test_bob_lists_alice_directory(self):
        """Bob requests directory listing from Alice."""
        # Create files on Alice's side
        self._create_test_file(self.alice_dir, 'alice_file.txt', b'alice data')

        result = [None]
        error = [None]

        def do_list():
            try:
                result[0] = self.bob_ft.list_dir(self.alice_dir, timeout=5.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_list)
        t.start()

        for _ in range(50):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'list_dir raised: %s' % error[0])
        files = {f['name']: f for f in result[0]}
        self.assertIn('alice_file.txt', files)

    def test_bob_downloads_from_alice(self):
        """Bob downloads a file from Alice."""
        content = b'File on Alice side'
        src_path = self._create_test_file(self.alice_dir, 'from_alice.txt', content)
        dst_path = os.path.join(self.bob_dir, 'to_bob.txt')

        error = [None]

        def do_get():
            try:
                self.bob_ft.get(src_path, dst_path, timeout=10.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_get)
        t.start()

        for _ in range(100):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'get raised: %s' % error[0])
        self.assertTrue(os.path.exists(dst_path))
        self.assertEqual(self._read_file(dst_path), content)

    def test_bob_uploads_to_alice(self):
        """Bob uploads a file to Alice."""
        content = b'File from Bob'
        src_path = self._create_test_file(self.bob_dir, 'from_bob.txt', content)
        dst_path = os.path.join(self.alice_dir, 'to_alice.txt')

        error = [None]

        def do_put():
            try:
                self.bob_ft.put(src_path, dst_path, timeout=10.0)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=do_put)
        t.start()

        for _ in range(100):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break

        t.join(timeout=2.0)
        self.assertIsNone(error[0], 'put raised: %s' % error[0])
        self.assertTrue(os.path.exists(dst_path))
        self.assertEqual(self._read_file(dst_path), content)


class TestBusyHandling(FileTransferTestCase):
    """Tests for concurrent transfer rejection."""

    @unittest.skip("Flaky: timing-dependent with mock transport, needs investigation")
    def test_concurrent_transfers_rejected(self):
        """Second transfer should fail with busy error while first is active."""
        # Create a larger file that takes time to transfer
        content = b'Z' * 100000
        src_path = self._create_test_file(self.bob_dir, 'slow.bin', content)
        dst_path1 = os.path.join(self.alice_dir, 'slow1.bin')
        dst_path2 = os.path.join(self.alice_dir, 'slow2.bin')

        error1 = [None]
        error2 = [None]
        started = threading.Event()

        def do_get1():
            try:
                started.set()
                self.alice_ft.get(src_path, dst_path1, timeout=30.0)
            except Exception as e:
                error1[0] = e

        def do_get2():
            started.wait()  # Wait for first to start
            time.sleep(0.1)  # Give it time to reserve active
            try:
                self.alice_ft.get(src_path, dst_path2, timeout=5.0)
            except FileTransferError as e:
                error2[0] = e

        t1 = threading.Thread(target=do_get1)
        t2 = threading.Thread(target=do_get2)
        t1.start()
        t2.start()

        for _ in range(500):
            self.alice_tunnel.tick()
            time.sleep(0.02)
            if not t1.is_alive() and not t2.is_alive():
                break

        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        # First should succeed
        self.assertIsNone(error1[0], 'First get raised: %s' % error1[0])
        self.assertTrue(os.path.exists(dst_path1))

        # Second should fail with busy
        self.assertIsNotNone(error2[0])
        self.assertEqual(error2[0].code, 'busy')


if __name__ == '__main__':
    unittest.main()
