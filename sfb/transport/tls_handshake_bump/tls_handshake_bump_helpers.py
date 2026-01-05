# -*- coding: ascii -*-
"""
Helpers for the TLS handshake bump client transport.
"""

from __future__ import absolute_import

import base64
import errno
import ssl

from ..transport_base import TransportError
from ...compat import text_type


_IN_PROGRESS = set([
    errno.EINPROGRESS,
    errno.EWOULDBLOCK,
    errno.EALREADY,
])
for name in ('WSAEINPROGRESS', 'WSAEWOULDBLOCK', 'WSAEALREADY'):
    value = getattr(errno, name, None)
    if value is not None:
        _IN_PROGRESS.add(value)

_TEMP_ERRORS = set([errno.EWOULDBLOCK, errno.EAGAIN])
for name in ('WSAEWOULDBLOCK', 'WSAEINTR'):
    value = getattr(errno, name, None)
    if value is not None:
        _TEMP_ERRORS.add(value)

_SOFT_CONNECT_ERRORS = set([errno.ECONNREFUSED])
for name in ('WSAECONNREFUSED',):
    value = getattr(errno, name, None)
    if value is not None:
        _SOFT_CONNECT_ERRORS.add(value)

_RESET_ERRORS = set([errno.ECONNRESET])
for name in ('WSAECONNRESET',):
    value = getattr(errno, name, None)
    if value is not None:
        _RESET_ERRORS.add(value)

_SSL_WANT_READ = getattr(ssl, 'SSL_ERROR_WANT_READ', None)
_SSL_WANT_WRITE = getattr(ssl, 'SSL_ERROR_WANT_WRITE', None)
_SSL_WANT_READ_ERROR = getattr(ssl, 'SSLWantReadError', None)
_SSL_WANT_WRITE_ERROR = getattr(ssl, 'SSLWantWriteError', None)


def _build_connect_request(target_hostport, proxy_auth=None):
    try:
        target_bytes = target_hostport.encode('ascii')
    except UnicodeError:
        raise TransportError('tls_bump_target must be ASCII when using tls_bump_http_proxy')
    lines = [
        b'CONNECT ' + target_bytes + b' HTTP/1.1',
        b'Host: ' + target_bytes,
    ]
    if proxy_auth is not None:
        try:
            auth_bytes = proxy_auth.encode('ascii')
        except UnicodeError:
            raise TransportError('tls_bump_http_proxy_auth must be ASCII')
        token = base64.b64encode(auth_bytes)
        lines.append(b'Proxy-Authorization: Basic ' + token)
    lines.append(b'')
    lines.append(b'')
    return b'\r\n'.join(lines)


def _build_https_request_parts(path):
    if not isinstance(path, text_type):
        raise ValueError('Path must be text')
    try:
        path_bytes = path.encode('ascii')
    except UnicodeError:
        raise ValueError('Path must be ASCII')
    prefix = b'GET ' + path_bytes + b' HTTP/1.1\r\nHost: '
    suffix = b'\r\nConnection: close\r\n\r\n'
    return prefix, suffix


def _build_https_request(sni_name, prefix, suffix):
    if not isinstance(sni_name, text_type):
        raise ValueError('SNI must be text')
    try:
        sni_bytes = sni_name.encode('ascii')
    except UnicodeError:
        raise ValueError('SNI must be ASCII')
    return prefix + sni_bytes + suffix


def _create_ssl_context():
    if not hasattr(ssl, 'SSLContext'):
        raise TransportError('SSLContext required for TLS bump transport')
    has_tls12 = getattr(ssl, 'HAS_TLSv1_2', False)
    proto_tls12 = getattr(ssl, 'PROTOCOL_TLSv1_2', None)
    if not has_tls12 and proto_tls12 is None:
        raise TransportError('TLS bump transport requires TLS 1.2 support')
    proto = getattr(ssl, 'PROTOCOL_TLS_CLIENT', None)
    if proto is None:
        if proto_tls12 is None:
            proto = getattr(ssl, 'PROTOCOL_TLSv1', ssl.PROTOCOL_SSLv23)
        else:
            proto = proto_tls12
    context = ssl.SSLContext(proto)
    if hasattr(context, 'check_hostname'):
        context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _wrap_socket(context, sock, sni_name):
    try:
        return context.wrap_socket(
            sock,
            server_hostname=sni_name,
            do_handshake_on_connect=False,
        )
    except TypeError:
        raise TransportError('TLS bump transport requires SNI support')


def _ssl_wants_read(exc):
    if _SSL_WANT_READ_ERROR is not None and isinstance(exc, _SSL_WANT_READ_ERROR):
        return True
    return getattr(exc, 'errno', None) == _SSL_WANT_READ


def _ssl_wants_write(exc):
    if _SSL_WANT_WRITE_ERROR is not None and isinstance(exc, _SSL_WANT_WRITE_ERROR):
        return True
    return getattr(exc, 'errno', None) == _SSL_WANT_WRITE


def _get_errno(exc):
    err = getattr(exc, 'errno', None)
    if err is None and getattr(exc, 'args', None):
        if exc.args:
            try:
                err = int(exc.args[0])
            except (TypeError, ValueError):
                err = None
    return err
