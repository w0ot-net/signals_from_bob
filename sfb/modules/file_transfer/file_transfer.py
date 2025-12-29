# -*- coding: ascii -*-
"""
File transfer module implementation.
"""

from __future__ import absolute_import

import os
import threading
import time
import hashlib

from ...channel import ChannelError
from ...compat import to_native_str
from ..base_module import BaseModule, RequestResponseMixin, ModuleError, blocking
from .file_transfer_control_messages import (
    file_list,
    file_list_ok,
    file_get,
    file_get_ok,
    file_put,
    file_put_ok,
    file_err,
    file_hash,
    file_hash_ok,
)

try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


class FileTransferError(ModuleError):
    """File transfer error."""
    pass


class TransferStats(object):
    """Statistics for a file transfer."""

    def __init__(self, size=0):
        self.size = size
        self.transferred = 0
        self.start_time = None
        self.end_time = None

    def start(self):
        """Mark transfer start."""
        self.start_time = time.time()

    def finish(self):
        """Mark transfer complete."""
        self.end_time = time.time()

    @property
    def duration(self):
        """Transfer duration in seconds."""
        if self.start_time is None:
            return 0
        end = self.end_time if self.end_time else time.time()
        return max(0.001, end - self.start_time)  # Avoid division by zero

    @property
    def bytes_per_sec(self):
        """Transfer rate in bytes per second."""
        return self.transferred / self.duration

    def format_rate(self):
        """Format transfer rate with appropriate unit (B/s, KB/s, MB/s)."""
        rate = self.bytes_per_sec
        if rate >= 1024 * 1024:
            return '%.2f MB/s' % (rate / (1024 * 1024))
        elif rate >= 1024:
            return '%.2f KB/s' % (rate / 1024)
        else:
            return '%.0f B/s' % rate

    def format_size(self):
        """Format transfer size with appropriate unit."""
        size = self.size
        if size >= 1024 * 1024:
            return '%.2f MB' % (size / (1024 * 1024))
        elif size >= 1024:
            return '%.2f KB' % (size / 1024)
        else:
            return '%d B' % size

    def update(self, delta):
        """Update transferred bytes."""
        self.transferred += delta

    def __repr__(self):
        return 'TransferStats(%s in %.2fs, %s)' % (
            self.format_size(), self.duration, self.format_rate()
        )


class FileTransferModule(RequestResponseMixin, BaseModule):
    """
    File transfer module (single active transfer).

    Provides file listing, download, and upload operations over tunnel
    channels. Only one transfer is active at a time.

    Handlers for incoming requests (handle_list, handle_get, handle_put)
    are marked @blocking so they run in separate threads and don't block
    the tunnel's message loop.
    """

    TYPE = 'file'

    @classmethod
    def register_commands(cls, subparsers, role):
        """Register CLI subcommands for file transfer.

        Commands are registered for the server (Bob) since he's the controller.
        """
        if role == 'server':
            list_p = subparsers.add_parser('list', help='List remote directory')
            list_p.add_argument('path', help='Remote directory path')
            list_p.add_argument('--timeout', type=float, default=None,
                               help='Operation timeout in seconds (default: no timeout)')

            get_p = subparsers.add_parser('get', help='Download file')
            get_p.add_argument('remote', help='Remote file path')
            get_p.add_argument('local', nargs='?', help='Local file path (default: same name)')
            get_p.add_argument('--timeout', type=float, default=None,
                               help='Operation timeout in seconds (default: no timeout)')

            put_p = subparsers.add_parser('put', help='Upload file')
            put_p.add_argument('local', help='Local file path')
            put_p.add_argument('remote', help='Remote file path')
            put_p.add_argument('--timeout', type=float, default=None,
                               help='Operation timeout in seconds (default: no timeout)')

    def __init__(self, tunnel, logger=None):
        super(FileTransferModule, self).__init__(tunnel, logger=logger)
        config = tunnel._config
        self._max_size = config.file_transfer_max_size
        self._chunk_size = config.file_transfer_chunk_size
        self._channel_open_timeout = config.channel_open_timeout
        self._hash_timeout = config.file_transfer_hash_timeout

        # Single active transfer enforcement
        self._active_lock = threading.Lock()
        self._active = False
        self._active_rid = None

        # Hash tracking for transfers
        self._hash_lock = threading.Lock()
        self._hash_events = {}
        self._hash_values = {}

        # Transfer statistics
        self._last_stats = None
        self._current_stats = None

    # -------------------------------------------------------------------------
    # Public API (called by user, runs in caller's thread)
    # -------------------------------------------------------------------------

    @property
    def last_stats(self):
        """Statistics from the last completed transfer."""
        return self._last_stats

    @property
    def current_stats(self):
        """Statistics for the current in-progress transfer."""
        return self._current_stats

    def list_dir(self, path, timeout=None):
        """Request a directory listing from the peer."""
        rid = self._alloc_rid()
        self._reserve_active(rid)
        try:
            pending = self._register_pending(rid)
            self.send_message(file_list(rid, path))
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'list_ok':
                return response.get('files', [])
            raise FileTransferError(
                response.get('code', 'io'),
                response.get('reason', 'error'),
            )
        finally:
            self._clear_active(rid)

    def get(self, remote_path, local_path=None, timeout=None):
        """Download a file from the peer."""
        rid = self._alloc_rid()
        self._reserve_active(rid)
        channel = None
        out_fp = None
        try:
            channel = self._tunnel.channel_manager.open_channel()
            if not channel.wait_open(timeout=timeout):
                raise FileTransferError('io', 'channel open failed')

            pending = self._register_pending(rid)
            self.send_message(file_get(rid, channel.id, remote_path))
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'err':
                raise FileTransferError(
                    response.get('code', 'io'),
                    response.get('reason', 'error'),
                )

            size = response.get('size')
            if size is None:
                raise FileTransferError('io', 'missing size')
            if not isinstance(size, integer_types) or size < 0:
                raise FileTransferError('io', 'invalid size')
            if self._max_size is not None and size > self._max_size:
                raise FileTransferError('too_large', 'size exceeds limit')

            dest_path = local_path
            if dest_path is None:
                dest_path = os.path.basename(remote_path)
            dest_path = os.path.abspath(dest_path)
            out_fp = open(dest_path, 'wb')

            stats = TransferStats(size)
            stats.start()
            self._current_stats = stats
            hash_obj = hashlib.sha256()
            self._recv_to_file(channel, out_fp, size, timeout, hash_obj=hash_obj, stats=stats)
            expected = self._wait_hash_value(rid, timeout)
            if expected != hash_obj.hexdigest():
                self.send_message(
                    file_err(rid, 'hash', 'hash mismatch', channel.id)
                )
                raise FileTransferError('hash', 'hash mismatch')
            self.send_message(file_hash_ok(rid, channel.id))
            stats.finish()
            self._last_stats = stats
            out_fp.close()
            out_fp = None
        finally:
            if out_fp is not None:
                out_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)
            self._clear_hash_state(rid)
            self._current_stats = None

    def put(self, local_path, remote_path, timeout=None):
        """Upload a file to the peer."""
        rid = self._alloc_rid()
        self._reserve_active(rid)
        channel = None
        in_fp = None
        try:
            src_path = os.path.abspath(local_path)
            size = os.path.getsize(src_path)
            if self._max_size is not None and size > self._max_size:
                raise FileTransferError('too_large', 'size exceeds limit')

            channel = self._tunnel.channel_manager.open_channel()
            if not channel.wait_open(timeout=timeout):
                raise FileTransferError('io', 'channel open failed')

            pending = self._register_pending(rid)
            self.send_message(file_put(rid, channel.id, remote_path, size))
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'err':
                raise FileTransferError(
                    response.get('code', 'io'),
                    response.get('reason', 'error'),
                )

            in_fp = open(src_path, 'rb')
            stats = TransferStats(size)
            stats.start()
            self._current_stats = stats
            hash_obj = hashlib.sha256()
            self._send_from_file(channel, in_fp, size, hash_obj=hash_obj, stats=stats,
                                 timeout=timeout)

            pending = self._register_pending(rid)
            self.send_message(file_hash(rid, channel.id, hash_obj.hexdigest()))
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'err':
                raise FileTransferError(
                    response.get('code', 'io'),
                    response.get('reason', 'error'),
                )
            stats.finish()
            self._last_stats = stats
        finally:
            if in_fp is not None:
                in_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)
            self._current_stats = None

    # -------------------------------------------------------------------------
    # Response handlers (non-blocking, signal waiters)
    # -------------------------------------------------------------------------

    def handle_list_ok(self, msg):
        """Handle list_ok response."""
        self._complete_pending(msg)

    def handle_get_ok(self, msg):
        """Handle get_ok response."""
        self._complete_pending(msg)

    def handle_put_ok(self, msg):
        """Handle put_ok response."""
        self._complete_pending(msg)

    def handle_hash_ok(self, msg):
        """Handle hash_ok response."""
        self._complete_pending(msg)

    def handle_err(self, msg):
        """Handle error response."""
        self._complete_pending(msg)

    def handle_hash(self, msg):
        """Handle incoming hash message."""
        rid = msg.get('rid')
        digest = msg.get('hash')
        alg = msg.get('alg')
        if rid is None or digest is None:
            return
        if alg not in (None, 'sha256'):
            self.send_message(file_err(rid, 'hash', 'unsupported hash', msg.get('ch')))
            return
        self._store_hash_value(rid, digest)

    # -------------------------------------------------------------------------
    # Request handlers (blocking, run in separate threads)
    # -------------------------------------------------------------------------

    @blocking
    def handle_list(self, msg):
        """Handle incoming list request."""
        rid = msg.get('rid')
        path = msg.get('path')
        if rid is None or path is None:
            return

        if not self._try_reserve_active(rid):
            self.send_message(
                file_err(rid, 'busy', 'transfer in progress')
            )
            return

        try:
            abs_path = os.path.abspath(path)
            try:
                entries = os.listdir(abs_path)
            except OSError as e:
                self.send_message(file_err(rid, 'not_found', to_native_str(e)))
                return
            files = []
            for name in entries:
                full = os.path.join(abs_path, name)
                try:
                    is_dir = os.path.isdir(full)
                    size = os.path.getsize(full) if os.path.isfile(full) else 0
                except OSError:
                    is_dir = False
                    size = 0
                files.append({
                    'name': name,
                    'size': size,
                    'dir': is_dir,
                })
            self.send_message(file_list_ok(rid, files))
        except Exception as e:
            self.send_message(file_err(rid, 'io', to_native_str(e)))
        finally:
            self._clear_active(rid)

    @blocking
    def handle_get(self, msg):
        """Handle incoming get request (send file to peer)."""
        rid = msg.get('rid')
        ch = msg.get('ch')
        path = msg.get('path')
        if rid is None or ch is None or path is None:
            return

        if not self._try_reserve_active(rid):
            self.send_message(
                file_err(rid, 'busy', 'transfer in progress', ch)
            )
            return

        channel = None
        in_fp = None
        try:
            abs_path = os.path.abspath(path)
            if not os.path.isfile(abs_path):
                self.send_message(file_err(rid, 'not_found', 'not found', ch))
                return
            size = os.path.getsize(abs_path)
            if self._max_size is not None and size > self._max_size:
                self.send_message(
                    file_err(rid, 'too_large', 'size exceeds limit', ch)
                )
                return
            channel = self._tunnel.channel_manager.get_channel(ch)
            if channel is None or not channel.wait_open(timeout=self._channel_open_timeout):
                self.send_message(file_err(rid, 'io', 'channel not open', ch))
                return
            self.send_message(file_get_ok(rid, ch, size))
            in_fp = open(abs_path, 'rb')
            hash_obj = hashlib.sha256()
            self._send_from_file(channel, in_fp, size, hash_obj=hash_obj)
            self.send_message(file_hash(rid, ch, hash_obj.hexdigest()))
        except FileTransferError as e:
            self.send_message(file_err(rid, e.code, e.reason, ch))
        except Exception as e:
            self.send_message(file_err(rid, 'io', to_native_str(e), ch))
        finally:
            if in_fp is not None:
                in_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    @blocking
    def handle_put(self, msg):
        """Handle incoming put request (receive file from peer)."""
        rid = msg.get('rid')
        ch = msg.get('ch')
        path = msg.get('path')
        size = msg.get('size')
        if rid is None or ch is None or path is None or size is None:
            return
        if not isinstance(size, integer_types) or size < 0:
            self.send_message(file_err(rid, 'io', 'invalid size', ch))
            return

        if not self._try_reserve_active(rid):
            self.send_message(
                file_err(rid, 'busy', 'transfer in progress', ch)
            )
            return

        channel = None
        out_fp = None
        try:
            if self._max_size is not None and size > self._max_size:
                self.send_message(
                    file_err(rid, 'too_large', 'size exceeds limit', ch)
                )
                return
            dest_path = os.path.abspath(path)
            channel = self._tunnel.channel_manager.get_channel(ch)
            if channel is None or not channel.wait_open(timeout=self._channel_open_timeout):
                self.send_message(file_err(rid, 'io', 'channel not open', ch))
                return
            out_fp = open(dest_path, 'wb')
            self.send_message(file_put_ok(rid, ch))
            hash_obj = hashlib.sha256()
            self._recv_to_file(channel, out_fp, size, timeout=None, hash_obj=hash_obj)
            expected = self._wait_hash_value(rid, timeout=self._hash_timeout)
            if expected != hash_obj.hexdigest():
                self.send_message(file_err(rid, 'hash', 'hash mismatch', ch))
                return
            self.send_message(file_hash_ok(rid, ch))
            out_fp.close()
            out_fp = None
        except FileTransferError as e:
            self.send_message(file_err(rid, e.code, e.reason, ch))
        except Exception as e:
            self.send_message(file_err(rid, 'io', to_native_str(e), ch))
        finally:
            if out_fp is not None:
                out_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    # -------------------------------------------------------------------------
    # File I/O helpers
    # -------------------------------------------------------------------------

    def _send_from_file(self, channel, fp, total_size, hash_obj=None, stats=None,
                        timeout=None):
        """Send file contents to channel."""
        remaining = total_size
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout

        while remaining > 0:
            chunk = fp.read(min(self._chunk_size, remaining))
            if not chunk:
                raise FileTransferError('io', 'unexpected EOF')
            if hash_obj is not None:
                hash_obj.update(chunk)

            # Calculate remaining time for this chunk
            chunk_timeout = None
            if deadline is not None:
                chunk_timeout = deadline - time.time()
                if chunk_timeout <= 0:
                    raise FileTransferError('io', 'send timeout')

            try:
                channel.write_all(chunk, timeout=chunk_timeout)
            except ChannelError as e:
                if e.code == 'timeout':
                    raise FileTransferError('io', 'send timeout')
                raise FileTransferError('io', e.message)

            remaining -= len(chunk)
            if stats is not None:
                stats.update(len(chunk))

    def _recv_to_file(self, channel, fp, total_size, timeout, hash_obj=None, stats=None):
        """Receive file contents from channel."""
        remaining = total_size
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout
        while remaining > 0:
            chunk_size = min(self._chunk_size, remaining)
            try:
                if deadline is None:
                    chunk = channel.read_exact(chunk_size)
                else:
                    remaining_time = deadline - time.time()
                    if remaining_time <= 0:
                        raise FileTransferError('io', 'timeout')
                    chunk = channel.read_exact(chunk_size, timeout=remaining_time)
            except ChannelError as e:
                if e.code == 'timeout':
                    raise FileTransferError('io', 'timeout')
                if e.code == 'closed':
                    raise FileTransferError('io', 'channel closed')
                raise FileTransferError('io', e.message)
            if hash_obj is not None:
                hash_obj.update(chunk)
            fp.write(chunk)
            remaining -= len(chunk)
            if stats is not None:
                stats.update(len(chunk))

    # -------------------------------------------------------------------------
    # Active transfer management
    # -------------------------------------------------------------------------

    def _reserve_active(self, rid):
        """Reserve active slot (raises if busy)."""
        with self._active_lock:
            if self._active:
                raise FileTransferError('busy', 'transfer in progress')
            self._active = True
            self._active_rid = rid

    def _try_reserve_active(self, rid):
        """Try to reserve active slot (returns False if busy)."""
        with self._active_lock:
            if self._active:
                return False
            self._active = True
            self._active_rid = rid
            return True

    def _clear_active(self, rid):
        """Clear active slot if we own it."""
        with self._active_lock:
            if self._active_rid == rid:
                self._active = False
                self._active_rid = None

    def _wait_hash_value(self, rid, timeout):
        """Wait for a hash value to arrive for this request."""
        with self._hash_lock:
            if rid in self._hash_values:
                return self._hash_values.pop(rid)
            event = threading.Event()
            self._hash_events[rid] = event
        if not event.wait(timeout=timeout):
            with self._hash_lock:
                self._hash_events.pop(rid, None)
            raise FileTransferError('io', 'hash timeout')
        with self._hash_lock:
            return self._hash_values.pop(rid, None)

    def _store_hash_value(self, rid, digest):
        """Store hash value and wake any waiter."""
        with self._hash_lock:
            self._hash_values[rid] = digest
            event = self._hash_events.pop(rid, None)
        if event is not None:
            event.set()

    def _clear_hash_state(self, rid):
        """Remove any stored hash value or waiter for this request."""
        with self._hash_lock:
            self._hash_values.pop(rid, None)
            self._hash_events.pop(rid, None)
