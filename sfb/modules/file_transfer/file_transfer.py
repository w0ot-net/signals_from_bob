# -*- coding: ascii -*-
"""
File transfer module implementation.
"""

from __future__ import absolute_import

import logging
import os
import tempfile
import threading
import time

from ...compat import text_type
from ...channel import ChannelError
from .file_transfer_control_messages import (
    file_list,
    file_list_ok,
    file_get,
    file_get_ok,
    file_put,
    file_put_ok,
    file_err,
)


class FileTransferError(Exception):
    """File transfer error."""
    def __init__(self, code, reason):
        Exception.__init__(self, reason)
        self.code = code
        self.reason = reason


class _PendingRequest(object):
    __slots__ = ('event', 'response')

    def __init__(self):
        self.event = threading.Event()
        self.response = None


class FileTransferModule(object):
    """File transfer module (single active transfer)."""

    TYPE = 'file'

    def __init__(self, tunnel, root=None, max_size=None, chunk_size=8192,
                 logger=None):
        self._tunnel = tunnel
        self._root = os.path.abspath(root or os.getcwd())
        self._max_size = max_size
        self._chunk_size = chunk_size
        self._logger = logger or logging.getLogger(__name__)

        self._lock = threading.Lock()
        self._active = False
        self._active_rid = None

        self._rid_lock = threading.Lock()
        self._next_rid = 1
        self._pending = {}

        tunnel.register_module(self.TYPE, self._handle_message)

    def list_dir(self, path, timeout=None):
        """Request a directory listing from the peer."""
        rid = self._alloc_rid()
        self._reserve_active(rid)
        try:
            pending = self._register_pending(rid)
            self._tunnel.control.send_message(file_list(rid, path))
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
        tmp_path = None
        out_fp = None
        try:
            channel = self._tunnel.channel_manager.open_channel()
            if not channel.wait_open(timeout=timeout):
                raise FileTransferError('io', 'channel open failed')

            pending = self._register_pending(rid)
            self._tunnel.control.send_message(
                file_get(rid, channel.id, remote_path)
            )
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'err':
                raise FileTransferError(
                    response.get('code', 'io'),
                    response.get('reason', 'error'),
                )

            size = response.get('size')
            if size is None:
                raise FileTransferError('io', 'missing size')
            if self._max_size is not None and size > self._max_size:
                self._tunnel.control.send_message(
                    file_err(rid, 'too_large', 'size exceeds limit', channel.id)
                )
                raise FileTransferError('too_large', 'size exceeds limit')

            dest_path = local_path
            if dest_path is None:
                dest_path = os.path.basename(remote_path)
            dest_path = self._normalize_local_path(dest_path)
            out_fp, tmp_path = self._open_temp_file(dest_path)

            self._recv_to_file(channel, out_fp, size, timeout)
            out_fp.close()
            out_fp = None
            self._replace_file(tmp_path, dest_path)
            tmp_path = None
        finally:
            if out_fp is not None:
                out_fp.close()
            if tmp_path is not None:
                self._safe_remove(tmp_path)
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    def put(self, local_path, remote_path, timeout=None):
        """Upload a file to the peer."""
        rid = self._alloc_rid()
        self._reserve_active(rid)
        channel = None
        in_fp = None
        try:
            src_path = self._normalize_local_path(local_path)
            size = os.path.getsize(src_path)
            if self._max_size is not None and size > self._max_size:
                raise FileTransferError('too_large', 'size exceeds limit')

            channel = self._tunnel.channel_manager.open_channel()
            if not channel.wait_open(timeout=timeout):
                raise FileTransferError('io', 'channel open failed')

            pending = self._register_pending(rid)
            self._tunnel.control.send_message(
                file_put(rid, channel.id, remote_path, size)
            )
            response = self._wait_response(rid, pending, timeout)
            if response.get('c') == 'err':
                raise FileTransferError(
                    response.get('code', 'io'),
                    response.get('reason', 'error'),
                )

            in_fp = open(src_path, 'rb')
            self._send_from_file(channel, in_fp, size)
        finally:
            if in_fp is not None:
                in_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    def _handle_message(self, msg):
        cmd = msg.get('c')
        rid = msg.get('rid')
        if not cmd or rid is None:
            return

        if cmd in ('list_ok', 'get_ok', 'put_ok', 'err'):
            pending = self._pending.pop(rid, None)
            if pending is not None:
                pending.response = msg
                pending.event.set()
            return

        if self._is_busy():
            self._tunnel.control.send_message(
                file_err(rid, 'busy', 'transfer in progress', msg.get('ch'))
            )
            return

        if cmd == 'list':
            self._handle_list_request(msg)
        elif cmd == 'get':
            self._handle_get_request(msg)
        elif cmd == 'put':
            self._handle_put_request(msg)

    def _handle_list_request(self, msg):
        rid = msg.get('rid')
        path = msg.get('path')
        if rid is None or path is None:
            return
        self._reserve_active(rid)
        try:
            abs_path = self._normalize_remote_path(path)
            try:
                entries = os.listdir(abs_path)
            except OSError as e:
                self._tunnel.control.send_message(
                    file_err(rid, 'not_found', str(e))
                )
                return
            files = []
            for name in entries:
                full = os.path.join(abs_path, name)
                files.append({
                    'name': name,
                    'size': os.path.getsize(full) if os.path.isfile(full) else 0,
                    'dir': os.path.isdir(full),
                })
            self._tunnel.control.send_message(file_list_ok(rid, files))
        finally:
            self._clear_active(rid)

    def _handle_get_request(self, msg):
        rid = msg.get('rid')
        ch = msg.get('ch')
        path = msg.get('path')
        if rid is None or ch is None or path is None:
            return
        self._reserve_active(rid)
        channel = None
        in_fp = None
        try:
            abs_path = self._normalize_remote_path(path)
            if not os.path.isfile(abs_path):
                self._tunnel.control.send_message(
                    file_err(rid, 'not_found', 'not found', ch)
                )
                return
            size = os.path.getsize(abs_path)
            if self._max_size is not None and size > self._max_size:
                self._tunnel.control.send_message(
                    file_err(rid, 'too_large', 'size exceeds limit', ch)
                )
                return
            channel = self._tunnel.channel_manager.get_channel(ch)
            if channel is None or not channel.wait_open(timeout=5.0):
                self._tunnel.control.send_message(
                    file_err(rid, 'io', 'channel not open', ch)
                )
                return
            self._tunnel.control.send_message(file_get_ok(rid, ch, size))
            in_fp = open(abs_path, 'rb')
            self._send_from_file(channel, in_fp, size)
        finally:
            if in_fp is not None:
                in_fp.close()
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    def _handle_put_request(self, msg):
        rid = msg.get('rid')
        ch = msg.get('ch')
        path = msg.get('path')
        size = msg.get('size')
        if rid is None or ch is None or path is None or size is None:
            return
        self._reserve_active(rid)
        channel = None
        out_fp = None
        tmp_path = None
        try:
            if self._max_size is not None and size > self._max_size:
                self._tunnel.control.send_message(
                    file_err(rid, 'too_large', 'size exceeds limit', ch)
                )
                return
            dest_path = self._normalize_remote_path(path)
            channel = self._tunnel.channel_manager.get_channel(ch)
            if channel is None or not channel.wait_open(timeout=5.0):
                self._tunnel.control.send_message(
                    file_err(rid, 'io', 'channel not open', ch)
                )
                return
            out_fp, tmp_path = self._open_temp_file(dest_path)
            self._tunnel.control.send_message(file_put_ok(rid, ch))
            self._recv_to_file(channel, out_fp, size, timeout=10.0)
            out_fp.close()
            out_fp = None
            self._replace_file(tmp_path, dest_path)
            tmp_path = None
        finally:
            if out_fp is not None:
                out_fp.close()
            if tmp_path is not None:
                self._safe_remove(tmp_path)
            if channel is not None:
                channel.close()
            self._clear_active(rid)

    def _send_from_file(self, channel, fp, total_size):
        remaining = total_size
        while remaining > 0:
            chunk = fp.read(min(self._chunk_size, remaining))
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                try:
                    sent = channel.write(chunk[offset:])
                except ChannelError:
                    time.sleep(0.01)
                    continue
                offset += sent
            remaining -= len(chunk)

    def _recv_to_file(self, channel, fp, total_size, timeout):
        remaining = total_size
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout
        while remaining > 0:
            if deadline is None:
                chunk = channel.read(min(self._chunk_size, remaining))
            else:
                remaining_time = deadline - time.time()
                if remaining_time <= 0:
                    raise FileTransferError('io', 'timeout')
                chunk = channel.read(
                    min(self._chunk_size, remaining),
                    timeout=remaining_time
                )
            if chunk is None:
                raise FileTransferError('io', 'timeout')
            if chunk == b'':
                raise FileTransferError('io', 'channel closed')
            fp.write(chunk)
            remaining -= len(chunk)

    def _alloc_rid(self):
        with self._rid_lock:
            rid = self._next_rid
            self._next_rid += 1
            return rid

    def _register_pending(self, rid):
        pending = _PendingRequest()
        self._pending[rid] = pending
        return pending

    def _wait_response(self, rid, pending, timeout):
        if not pending.event.wait(timeout=timeout):
            self._pending.pop(rid, None)
            raise FileTransferError('io', 'timeout')
        return pending.response or {}

    def _reserve_active(self, rid):
        with self._lock:
            if self._active:
                raise FileTransferError('busy', 'transfer in progress')
            self._active = True
            self._active_rid = rid

    def _clear_active(self, rid):
        with self._lock:
            if self._active_rid == rid:
                self._active = False
                self._active_rid = None

    def _is_busy(self):
        with self._lock:
            return self._active

    def _normalize_remote_path(self, path):
        return self._normalize_path(path)

    def _normalize_local_path(self, path):
        return self._normalize_path(path)

    def _normalize_path(self, path):
        if not isinstance(path, text_type):
            raise FileTransferError('invalid_path', 'path must be text')
        norm = os.path.normpath(path)
        if self._has_traversal(norm):
            raise FileTransferError('invalid_path', 'path traversal rejected')
        if os.path.isabs(norm):
            candidate = os.path.abspath(norm)
        else:
            candidate = os.path.abspath(os.path.join(self._root, norm))
        if not self._is_within_root(candidate):
            raise FileTransferError('invalid_path', 'path outside root')
        return candidate

    def _has_traversal(self, path):
        sep = os.sep
        alt = os.path.altsep
        check = path
        if alt:
            check = check.replace(alt, sep)
        parts = check.split(sep)
        return '..' in parts

    def _is_within_root(self, path):
        root = os.path.normcase(self._root)
        candidate = os.path.normcase(path)
        if candidate == root:
            return True
        return candidate.startswith(root + os.sep)

    def _open_temp_file(self, dest_path):
        dest_dir = os.path.dirname(dest_path)
        fd, tmp_path = tempfile.mkstemp(prefix='.sfb-', dir=dest_dir)
        return os.fdopen(fd, 'wb'), tmp_path

    def _replace_file(self, src, dst):
        try:
            os.replace(src, dst)
        except AttributeError:
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(src, dst)

    def _safe_remove(self, path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
