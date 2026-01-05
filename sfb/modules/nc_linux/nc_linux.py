# -*- coding: ascii -*-
"""NC Linux module implementation."""

from __future__ import absolute_import

import errno
import logging
import os
import socket
import sys
import threading

try:
    import fcntl
except ImportError:
    fcntl = None

from ..base_module import BaseModule, RequestResponseMixin, ModuleError, blocking
from ...compat import PY2, integer_types, text_type, to_native_str
from ...logging_util import get_logger, log_event
from ... import time_provider
from ...channel import ChannelError
from .nc_linux_control_messages import T_NC, nc_bind, nc_bind_ok, nc_err
from .nc_linux_pump import pump_fd_to_channel, pump_channel_to_fd


class NcLinuxError(ModuleError):
    """NC Linux module error."""
    pass


class _BoundFd(object):
    __slots__ = ('fd', 'label', '_close_func')

    def __init__(self, fd, label, close_func):
        self.fd = fd
        self.label = label
        self._close_func = close_func

    def close(self):
        if self._close_func is None:
            return
        try:
            self._close_func()
        except Exception:
            pass
        self._close_func = None


class _NcConnection(object):
    __slots__ = (
        '_rid', '_ch', '_channel', '_bound', '_logger', '_config', '_side',
        '_stop_event', '_threads', '_lock', '_pump_info', '_done', '_closed',
        '_on_close',
    )

    def __init__(self, rid, ch, channel, bound, logger, config, side,
                 on_close=None):
        self._rid = rid
        self._ch = ch
        self._channel = channel
        self._bound = bound
        self._logger = logger
        self._config = config
        self._side = side
        self._stop_event = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._pump_info = {}
        self._done = threading.Event()
        self._closed = False
        self._on_close = on_close

    @property
    def channel(self):
        return self._channel

    def start(self):
        t1 = threading.Thread(
            target=pump_fd_to_channel,
            args=(
                self._bound.fd,
                self._channel,
                self._config,
                self._logger,
                self._stop_event,
                self._rid,
                self._ch,
                self._side,
                self._bound.label,
            ),
            kwargs={
                'eof_callback': self._safe_close_write,
                'stop_callback': self._on_pump_stop,
            },
            name='nc-fd-to-channel-%d' % self._ch,
        )
        t2 = threading.Thread(
            target=pump_channel_to_fd,
            args=(
                self._channel,
                self._bound.fd,
                self._config,
                self._logger,
                self._stop_event,
                self._rid,
                self._ch,
                self._side,
                self._bound.label,
            ),
            kwargs={'stop_callback': self._on_pump_stop},
            name='nc-channel-to-fd-%d' % self._ch,
        )
        t1.daemon = True
        t2.daemon = True
        self._threads = [t1, t2]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop_event.set()
        try:
            self._channel.close()
        except Exception:
            pass

    def wait(self, timeout=None):
        return self._done.wait(timeout=timeout)

    def join(self, timeout=None):
        for t in list(self._threads):
            t.join(timeout=timeout)

    def _safe_close_write(self):
        try:
            self._channel.close_write()
        except ChannelError:
            pass
        except Exception:
            pass

    def _close_bound(self):
        if self._bound is None:
            return
        self._bound.close()
        self._bound = None

    def _finalize(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._channel.close()
        except Exception:
            pass
        self._close_bound()
        self._done.set()
        if self._on_close is not None:
            try:
                self._on_close(self._ch)
            except Exception:
                pass

    def _on_pump_stop(self, info):
        direction = info.get('direction')
        with self._lock:
            if direction:
                self._pump_info[direction] = info
            fatal = bool(info.get('fatal'))
        if fatal:
            try:
                self._channel.close()
            except Exception:
                pass
        with self._lock:
            if len(self._pump_info) < 2:
                return
        self._finalize()


class NcLinuxModule(RequestResponseMixin, BaseModule):
    """
    Bind a tunnel channel to a local file descriptor (linux only).
    """

    TYPE = T_NC
    DEFAULT_COMMAND = None
    REQUIRES_COMMAND = False
    REMOTE_MODULE = 'nc_linux'
    USES_SUBCOMMANDS = False

    @classmethod
    def register_commands(cls, subparsers, role, config=None):
        if role != 'server':
            return
        timeout_default = None
        if config is not None:
            timeout_default = config.nc_linux_bind_timeout
        group = subparsers.add_argument_group(
            'nc_linux options',
            'Bind a tunnel channel between Bob and Alice using fd specs.\n'
            'FD spec formats: fd number, /path, host:port, [::1]:port.\n'
            'Examples: --local 3 --remote /tmp/data.txt',
        )
        group.add_argument(
            '--local',
            required=True,
            metavar='SPEC',
            help='Bob-side fd spec (fd number, path, host:port)',
        )
        group.add_argument(
            '--remote',
            required=True,
            metavar='SPEC',
            help='Alice-side fd spec (fd number, path, host:port)',
        )
        group.add_argument(
            '--timeout',
            type=float,
            default=timeout_default,
            help='Bind timeout in seconds (default: %s)' % timeout_default,
        )

    @classmethod
    def run_command(cls, args, tunnel, logger):
        if not _is_linux():
            log_event(
                logger,
                logging.ERROR,
                'nc.unsupported_platform',
                'nc_linux is only supported on linux',
                lambda: {'side': 'bob'},
            )
            return 1
        module = cls(tunnel, logger=logger)
        timeout = getattr(args, 'timeout', None)
        local_spec = getattr(args, 'local', None)
        remote_spec = getattr(args, 'remote', None)
        try:
            conn = module.bind(remote_spec, local_spec, timeout=timeout)
            while tunnel.connected and not conn.wait(timeout=0.5):
                time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)
            return 0
        finally:
            module.shutdown()

    def __init__(self, tunnel, logger=None):
        super(NcLinuxModule, self).__init__(tunnel, logger=logger)
        self._config = tunnel._config
        self._connections = {}
        self._connections_lock = threading.Lock()

    def shutdown(self):
        with self._connections_lock:
            connections = list(self._connections.values())
        for conn in connections:
            conn.stop()
        for conn in connections:
            conn.join(timeout=self._config.module_shutdown_timeout)
        with self._connections_lock:
            self._connections.clear()
        super(NcLinuxModule, self).shutdown()

    def bind(self, remote_spec, local_spec, timeout=None):
        if not _is_linux():
            raise NcLinuxError('not_linux', 'nc_linux is only supported on linux')

        remote_spec = _coerce_spec(remote_spec)
        if remote_spec is None:
            raise NcLinuxError('invalid_spec', 'missing remote fd spec')

        channel = self._tunnel.channel_manager.open_channel()
        if not channel.wait_open(timeout=self._config.channel_open_timeout):
            channel.close()
            raise NcLinuxError('channel_open_failed', 'channel open failed')

        try:
            local_bound = _open_spec(local_spec, self._config)
        except NcLinuxError as exc:
            channel.close()
            raise

        rid = self._alloc_rid()
        pending = self._register_pending(rid)
        self.send_message(nc_bind(rid, channel.id, remote_spec))
        log_event(
            self._logger,
            logging.INFO,
            'nc.bind_send',
            'Bind request sent',
            lambda: {'rid': rid, 'ch': channel.id, 'side': 'bob'},
        )
        try:
            resp = self._wait_response(rid, pending, timeout=timeout)
        except ModuleError:
            local_bound.close()
            channel.close()
            raise

        cmd = resp.get('c')
        if cmd != 'bind_ok':
            local_bound.close()
            channel.close()
            if cmd == 'err':
                code = resp.get('code', 'bind_failed')
                reason = resp.get('reason', 'bind failed')
            else:
                code = 'bind_failed'
                reason = 'unexpected bind response'
            raise NcLinuxError(code, reason)

        conn = _NcConnection(
            rid=rid,
            ch=channel.id,
            channel=channel,
            bound=local_bound,
            logger=self._logger,
            config=self._config,
            side='bob',
            on_close=self._on_connection_close,
        )
        with self._connections_lock:
            self._connections[channel.id] = conn
        conn.start()
        log_event(
            self._logger,
            logging.INFO,
            'nc.bind_ok_recv',
            'Bind ok received',
            lambda: {'rid': rid, 'ch': channel.id, 'side': 'bob'},
        )
        return conn

    def _on_connection_close(self, ch):
        with self._connections_lock:
            self._connections.pop(ch, None)

    @blocking
    def handle_bind(self, msg):
        rid = msg.get('rid')
        ch = msg.get('ch')
        spec = msg.get('fd')

        log_event(
            self._logger,
            logging.INFO,
            'nc.bind_recv',
            'Bind request received',
            lambda: {'rid': rid, 'ch': ch, 'side': 'alice'},
        )

        if not _is_linux():
            self._send_err(rid, ch, 'not_linux', 'nc_linux is linux-only')
            self._close_channel(ch)
            return
        if not self._tunnel._is_initiator:
            self._send_err(rid, ch, 'invalid_side', 'bind only supported on alice')
            self._close_channel(ch)
            return
        if rid is None or ch is None:
            self._send_err(rid, ch, 'invalid_request', 'missing rid or ch')
            self._close_channel(ch)
            return
        if ch == 0:
            self._send_err(rid, ch, 'invalid_request', 'control channel not allowed')
            self._close_channel(ch)
            return

        channel = self._tunnel.channel_manager.get_channel(ch)
        if channel is None:
            self._send_err(rid, ch, 'channel_missing', 'channel not found')
            self._close_channel(ch)
            return
        if not channel.wait_open(timeout=self._config.channel_open_timeout):
            self._send_err(rid, ch, 'channel_open_failed', 'channel open failed')
            self._close_channel(ch)
            return

        with self._connections_lock:
            if ch in self._connections:
                self._send_err(rid, ch, 'already_bound', 'channel already bound')
                self._close_channel(ch)
                return

        try:
            bound = _open_spec(spec, self._config)
        except NcLinuxError as exc:
            self._send_err(rid, ch, exc.code, exc.reason)
            self._close_channel(ch)
            return

        conn = _NcConnection(
            rid=rid,
            ch=ch,
            channel=channel,
            bound=bound,
            logger=self._logger,
            config=self._config,
            side='alice',
            on_close=self._on_connection_close,
        )
        with self._connections_lock:
            self._connections[ch] = conn

        conn.start()
        self.send_message(nc_bind_ok(rid, ch))
        log_event(
            self._logger,
            logging.INFO,
            'nc.bind_ok_send',
            'Bind ok sent',
            lambda: {'rid': rid, 'ch': ch, 'side': 'alice'},
        )

    def handle_bind_ok(self, msg):
        if not self._complete_pending(msg):
            log_event(
                self._logger,
                logging.DEBUG,
                'nc.bind_ok_unexpected',
                'Unexpected bind_ok',
                lambda: {'rid': msg.get('rid'), 'ch': msg.get('ch')},
            )

    def handle_err(self, msg):
        if not self._complete_pending(msg):
            log_event(
                self._logger,
                logging.DEBUG,
                'nc.err_unexpected',
                'Unexpected error response',
                lambda: {
                    'rid': msg.get('rid'),
                    'ch': msg.get('ch'),
                    'code': msg.get('code'),
                },
            )

    def _send_err(self, rid, ch, code, reason):
        self.send_message(nc_err(rid, ch, code, reason))
        log_event(
            self._logger,
            logging.WARNING,
            'nc.bind_err_send',
            'Bind error sent',
            lambda: {'rid': rid, 'ch': ch, 'code': code},
        )

    def _close_channel(self, ch):
        try:
            self._tunnel.channel_manager.close_channel(ch)
        except Exception:
            pass


def _is_linux():
    return sys.platform.startswith('linux')


def _coerce_spec(spec):
    if isinstance(spec, integer_types):
        return spec
    if spec is None:
        return None
    if isinstance(spec, text_type):
        return spec
    if isinstance(spec, bytes):
        if PY2:
            return spec
        try:
            return spec.decode('utf-8')
        except Exception:
            return spec.decode('utf-8', 'replace')
    return to_native_str(spec)


def _parse_host_port(spec):
    if spec.startswith('['):
        end = spec.find(']')
        if end == -1:
            return None
        if len(spec) <= end + 2 or spec[end + 1] != ':':
            return None
        host = spec[1:end]
        port_text = spec[end + 2:]
    else:
        if spec.count(':') != 1:
            return None
        host, port_text = spec.rsplit(':', 1)
    if not host or not port_text:
        return None
    try:
        port = int(port_text)
    except Exception:
        return None
    if port < 1 or port > 65535:
        return None
    return host, port


def _parse_spec(spec):
    spec = _coerce_spec(spec)
    if spec is None:
        raise NcLinuxError('invalid_spec', 'missing fd spec')
    if isinstance(spec, integer_types):
        return ('fd', int(spec))
    if not isinstance(spec, text_type):
        spec = to_native_str(spec)
    if hasattr(spec, 'isdigit') and spec.isdigit():
        return ('fd', int(spec))
    if spec.startswith('['):
        host_port = _parse_host_port(spec)
        if host_port:
            return ('addr', host_port)
    if '/' in spec:
        return ('path', spec)
    host_port = _parse_host_port(spec)
    if host_port:
        return ('addr', host_port)
    return ('path', spec)


def _set_nonblocking(fd):
    if fcntl is None:
        return
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if flags & os.O_NONBLOCK:
            return
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except Exception:
        pass


def _open_spec(spec, config):
    if not _is_linux():
        raise NcLinuxError('not_linux', 'nc_linux is linux-only')
    if fcntl is None:
        raise NcLinuxError('not_linux', 'fcntl unavailable')

    kind, value = _parse_spec(spec)
    if kind == 'fd':
        fd = int(value)
        if fd < 0:
            raise NcLinuxError('invalid_fd', 'invalid fd')
        try:
            os.fstat(fd)
        except OSError:
            raise NcLinuxError('invalid_fd', 'bad file descriptor')
        _set_nonblocking(fd)
        return _BoundFd(fd, 'fd:%d' % fd, lambda: os.close(fd))

    if kind == 'path':
        flags = os.O_RDWR
        flags |= getattr(os, 'O_NONBLOCK', 0)
        try:
            fd = os.open(value, flags)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                try:
                    fd = os.open(value, flags | os.O_CREAT, 0o666)
                except OSError as exc2:
                    raise NcLinuxError('open_failed', to_native_str(exc2))
            else:
                raise NcLinuxError('open_failed', to_native_str(exc))
        _set_nonblocking(fd)
        return _BoundFd(fd, value, lambda: os.close(fd))

    if kind == 'addr':
        host, port = value
        timeout = getattr(config, 'nc_linux_connect_timeout', 10.0)
        sock = _connect_tcp(host, port, timeout)
        sock.setblocking(False)
        return _BoundFd(sock.fileno(), '%s:%d' % (host, port), sock.close)

    raise NcLinuxError('invalid_spec', 'unsupported fd spec')


def _connect_tcp(host, port, timeout):
    last_error = None
    try:
        addrinfos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except Exception as exc:
        raise NcLinuxError('connect_failed', to_native_str(exc))
    for family, socktype, proto, _canon, sockaddr in addrinfos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            sock.settimeout(None)
            return sock
        except Exception as exc:
            last_error = exc
            try:
                sock.close()
            except Exception:
                pass
    if last_error is None:
        raise NcLinuxError('connect_failed', 'connect failed')
    raise NcLinuxError('connect_failed', to_native_str(last_error))
