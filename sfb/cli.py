# -*- coding: ascii -*-
"""
Generic CLI for sfb tunnel.

Provides a unified entry point supporting:
- Roles: server (bob) or client (alice)
- Transports: dns (extensible)
- Modules: file_transfer, socks, port_fwd_server, port_fwd_relay, etc.
"""

from __future__ import absolute_import

import argparse
import base64
import errno
import logging
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zlib

from .config import Config, DNS_STANDARD_SIZE
from .compat import byte_at, text_type
from .crypto import Plain, RC4, SHA256, XOR
from .logging_util import (
    StructuredLogFormatter,
    add_component_filters,
    add_sqlite_handler,
    get_logger,
    log_event,
)
from .transport import (
    TransportError,
    get_transport_class,
    get_transport_names,
    load_lossy,
)
from .tunnel.base_tunnel import TunnelError, TunnelState
from .tunnel.module_loader import ModuleLoadError
from .modules import get_cli_module_class, list_cli_modules
from .modules.base_module import ModuleError
from .utils import parse_host_port
from . import time_provider


# Role aliases
ROLE_ALIASES = {
    'bob': 'server',
    'alice': 'client',
    'server': 'server',
    'client': 'client',
}

_DB_LOG_DEFAULT = object()
_CPROFILE_DEFAULT = object()
_SFB_FLAT_DEFAULT = object()
_STAGER_DEFAULT = object()


def _print_error(message):
    prefix = 'ERROR: '
    if sys.stderr.isatty():
        sys.stderr.write('\x1b[31m' + prefix + message + '\x1b[0m\n')
    else:
        sys.stderr.write(prefix + message + '\n')
    sys.stderr.flush()


def _print_warning(message):
    prefix = 'WARNING: '
    if sys.stderr.isatty():
        sys.stderr.write('\x1b[33m' + prefix + message + '\x1b[0m\n')
    else:
        sys.stderr.write(prefix + message + '\n')
    sys.stderr.flush()


def _default_cprofile_dir():
    base_dir = '/tmp'
    if os.path.isdir(base_dir) and os.access(base_dir, os.W_OK):
        return base_dir
    return tempfile.gettempdir()


def _cprofile_default_filename(role, transport):
    role = role or 'unknown'
    transport = transport or 'unknown'
    timestamp = time.strftime(
        '%Y%m%d_%H%M%S',
        time.localtime(time_provider.wall_time()),
    )
    return 'sfb_%s_%s_%s_%s.prof' % (role, transport, timestamp, os.getpid())


def _resolve_cprofile_path(value, role, transport):
    if value is None:
        return None
    filename = _cprofile_default_filename(role, transport)
    if value is _CPROFILE_DEFAULT or value == '':
        path = os.path.join(_default_cprofile_dir(), filename)
    else:
        path = value
        if os.path.isdir(path):
            path = os.path.join(path, filename)
    return os.path.abspath(path)


def _ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if not parent:
        return
    try:
        os.makedirs(parent)
    except OSError as e:
        if e.errno != errno.EEXIST or not os.path.isdir(parent):
            raise


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

def _python_minifier_available():
    try:
        import python_minifier
    except ImportError:
        return False
    return True


def _build_sfb_flat(transport, minify):
    root = _repo_root()
    script_path = os.path.join(root, 'scripts', 'flatten.py')
    manifest_path = os.path.join(root, 'doc', 'flatten_manifest.txt')
    output_path = os.path.join(root, 'sfb_flat.py')
    python_bin = sys.executable
    if not os.path.isfile(script_path):
        _print_error('flatten script not found: %s' % script_path)
        return None
    if not os.path.isfile(manifest_path):
        _print_error('flatten manifest not found: %s' % manifest_path)
        return None
    if not python_bin:
        _print_error('Unable to resolve Python executable for flattening')
        return None
    cmd = [
        python_bin,
        script_path,
        '--manifest',
        manifest_path,
        '--output',
        output_path,
        '--strip-logs',
        '--alice',
        '--transport',
        transport,
    ]
    if minify:
        cmd.append('--minify')
    try:
        subprocess.check_call(cmd)
    except (OSError, subprocess.CalledProcessError) as exc:
        _print_error('Failed to generate sfb_flat.py: %s' % exc)
        return None
    if not os.path.isfile(output_path):
        _print_error('sfb_flat.py was not created: %s' % output_path)
        return None
    return output_path


def _auto_flatten_sfb_flat(transport):
    minify = _python_minifier_available()
    if not minify:
        _print_warning('python-minifier not installed; flattening without minify')
    return _build_sfb_flat(transport, minify=minify)


def _gzip_bytes(data):
    compressor = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def _split_chunks(data, chunk_size):
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


def _calc_flat_payload_cap(base_domain, cname_label, label_max_len):
    from .transport.dns import dns_codec
    base_domain = (base_domain or '').strip().lower().strip('.')
    if not base_domain:
        raise ValueError('base_domain required')
    cname_label = (cname_label or '').strip().strip('.')
    if cname_label:
        cname_suffix = '%s.%s' % (cname_label, base_domain)
    else:
        cname_suffix = base_domain
    qname = 'flat0.%05d.%s' % (1, base_domain)
    qname_wire_len = len(dns_codec.encode_name(qname))
    payload_cap, _ = dns_codec.calc_cname_response_payload_cap(
        qname_wire_len,
        DNS_STANDARD_SIZE,
        cname_suffix,
        label_max_len,
        opt_record_len=0,
    )
    if payload_cap is None or payload_cap <= 0:
        raise ValueError('stager payload cap unavailable for %s' % qname)
    return payload_cap


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('must be a positive integer')
    if value <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return value


def _percent_in_range(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('must be a percentage in [0, 100]')
    if value < 0 or value > 100:
        raise argparse.ArgumentTypeError('must be a percentage in [0, 100]')
    return value


def _handle_tls_bump_generate_cert(parsed):
    if not getattr(parsed, 'tls_bump_generate_cert', None):
        return None
    cn_len = parsed.tls_bump_generate_cert
    try:
        cn_len = int(cn_len)
    except (TypeError, ValueError):
        _print_error('CN length must be an integer')
        return 2
    if cn_len <= 0:
        _print_error('CN length must be > 0')
        return 2
    try:
        base_len = cn_len
        if base_len > 64:
            base_len = 64
        cert_der = _generate_tls_bump_cert_der(base_len)
        patched_der, offsets = _patch_tls_bump_cert_der(cert_der, cn_len)
        template_path = _tls_bump_template_path()
        _write_tls_bump_cert_template(template_path, cn_len, offsets, patched_der)
    except (IOError, OSError, ValueError) as exc:
        _print_error(str(exc))
        return 2
    return 0


def _tls_bump_template_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(
        base_dir,
        'transport',
        'tls_handshake_bump',
        'tls_handshake_bump_cert_template.py',
    )


def _generate_tls_bump_cert_der(cn_len):
    cn_text = 'a' * cn_len
    temp_dir = tempfile.mkdtemp(prefix='tls_bump_cert_')
    try:
        key_path = os.path.join(temp_dir, 'key.pem')
        cert_pem = os.path.join(temp_dir, 'cert.pem')
        cert_der = os.path.join(temp_dir, 'cert.der')
        _run_openssl([
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-nodes',
            '-days',
            '1',
            '-subj',
            '/CN=%s' % cn_text,
            '-keyout',
            key_path,
            '-out',
            cert_pem,
        ])
        _run_openssl([
            'x509',
            '-in',
            cert_pem,
            '-outform',
            'der',
            '-out',
            cert_der,
        ])
        with open(cert_der, 'rb') as handle:
            return handle.read()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_openssl(args):
    try:
        subprocess.check_call(['openssl'] + list(args))
    except OSError as exc:
        if getattr(exc, 'errno', None) == errno.ENOENT:
            raise ValueError('openssl not found in PATH')
        raise
    except subprocess.CalledProcessError as exc:
        raise ValueError('openssl failed: %s' % exc)


class _Asn1Node(object):
    __slots__ = ('tag', 'value', 'children', 'is_cn_value')

    def __init__(self, tag, value=None, children=None):
        self.tag = tag
        self.value = value
        self.children = children
        self.is_cn_value = False


_OID_COMMON_NAME = b'\x55\x04\x03'


def _patch_tls_bump_cert_der(cert_der, cn_len):
    root = _parse_der(cert_der)
    cn_nodes = []
    _mark_cn_nodes(root, cn_nodes)
    if len(cn_nodes) != 2:
        raise ValueError('Expected 2 CN entries, found %d' % len(cn_nodes))
    cn_bytes = b'a' * cn_len
    for node in cn_nodes:
        if node.children is not None:
            raise ValueError('CN node must be primitive')
        node.value = cn_bytes
    patched_der, offsets = _encode_node(root)
    if len(offsets) != len(cn_nodes):
        raise ValueError('CN offset count mismatch')
    return patched_der, tuple(sorted(offsets))


def _mark_cn_nodes(node, found):
    if node.children is not None:
        if node.tag == 0x30 and len(node.children) >= 2:
            first = node.children[0]
            second = node.children[1]
            if first.tag == 0x06 and first.value == _OID_COMMON_NAME:
                second.is_cn_value = True
                found.append(second)
        for child in node.children:
            _mark_cn_nodes(child, found)


def _parse_der(data):
    nodes, offset = _parse_der_sequence(data, 0, len(data))
    if offset != len(data):
        raise ValueError('DER parse trailing data')
    if len(nodes) != 1:
        raise ValueError('DER parse expected single root')
    return nodes[0]


def _parse_der_sequence(data, offset, end):
    nodes = []
    while offset < end:
        node, offset = _parse_der_node(data, offset, end)
        nodes.append(node)
    if offset != end:
        raise ValueError('DER parse length mismatch')
    return nodes, offset


def _parse_der_node(data, offset, end):
    if offset >= end:
        raise ValueError('DER tag truncated')
    tag = byte_at(data, offset)
    length, len_len = _read_der_length(data, offset + 1, end)
    value_start = offset + 1 + len_len
    value_end = value_start + length
    if value_end > end:
        raise ValueError('DER value truncated')
    if tag & 0x20:
        children, _ = _parse_der_sequence(data, value_start, value_end)
        node = _Asn1Node(tag, children=children)
    else:
        node = _Asn1Node(tag, value=data[value_start:value_end])
    return node, value_end


def _read_der_length(data, offset, end):
    if offset >= end:
        raise ValueError('DER length truncated')
    first = byte_at(data, offset)
    if first & 0x80 == 0:
        return first, 1
    num = first & 0x7f
    if num == 0:
        raise ValueError('DER indefinite length unsupported')
    if offset + 1 + num > end:
        raise ValueError('DER length truncated')
    length = 0
    for i in range(num):
        length = (length << 8) | byte_at(data, offset + 1 + i)
    return length, 1 + num


def _encode_node(node):
    if node.children is not None:
        parts = []
        offsets = []
        cursor = 0
        for child in node.children:
            child_bytes, child_offsets = _encode_node(child)
            parts.append(child_bytes)
            for off in child_offsets:
                offsets.append(cursor + off)
            cursor += len(child_bytes)
        value_bytes = b''.join(parts)
    else:
        value_bytes = node.value or b''
        offsets = [0] if node.is_cn_value else []
    length_bytes = _encode_der_length(len(value_bytes))
    header = struct.pack('!B', node.tag) + length_bytes
    header_len = len(header)
    offsets = [header_len + off for off in offsets]
    return header + value_bytes, offsets


def _encode_der_length(length):
    if length < 0:
        raise ValueError('DER length invalid')
    if length < 128:
        return struct.pack('!B', length)
    buf = bytearray()
    while length:
        buf.append(length & 0xff)
        length >>= 8
    buf.reverse()
    return struct.pack('!B', 0x80 | len(buf)) + bytes(buf)


def _write_tls_bump_cert_template(path, cn_len, offsets, cert_der):
    b64_text = base64.b64encode(cert_der).decode('ascii')
    chunks = []
    width = 76
    for i in range(0, len(b64_text), width):
        chunks.append(b64_text[i:i + width])
    offsets_text = ', '.join(str(offset) for offset in offsets)
    if len(offsets) == 1:
        offsets_text += ','
    lines = [
        '# -*- coding: ascii -*-',
        '"""',
        'TLS handshake bump certificate template data.',
        '',
        'This module is data-only so a generator script can update it without touching',
        'runtime logic.',
        '"""',
        '',
        '# Generated by --tls-bump-generate-cert.',
        'CN_LEN = %d' % cn_len,
        'CN_OFFSETS = (%s)' % offsets_text,
        '',
        'CERT_TEMPLATE_DER_B64 = (',
    ]
    for chunk in chunks:
        lines.append("    b'%s'" % chunk)
    lines.append(')')
    lines.append('')
    with open(path, 'w') as handle:
        handle.write('\n'.join(lines))


def _has_arg_prefix(args, prefix):
    for item in args:
        if item == prefix or item.startswith(prefix + '='):
            return True
    return False


def normalize_role(role):
    """Normalize role name (bob->server, alice->client)."""
    role = role.lower()
    if role not in ROLE_ALIASES:
        raise ValueError('Unknown role: %s (use: server, client, bob, alice)' % role)
    return ROLE_ALIASES[role]


def add_common_args(parser, config, require_domain=True, require_role=True):
    """Add arguments shared by all roles."""
    parser.add_argument(
        '--role', required=require_role,
        help='Role: server (bob) or client (alice)'
    )
    parser.add_argument(
        '--transport', default=config.transport_default,
        choices=get_transport_names(),
        help='Transport type (default: %s)' % config.transport_default
    )
    parser.add_argument(
        '--max-in-flight',
        dest='max_in_flight', type=int, default=config.max_in_flight,
        help='Max in-flight packets (1-256, default: %s)' %
             config.max_in_flight
    )
    parser.add_argument(
        '--loss',
        type=_percent_in_range,
        default=0.0,
        metavar='<percent>',
        help='Packet loss percent for both directions (0-100). '
             'Overridden by --rx-loss/--tx-loss.'
    )
    parser.add_argument(
        '--rx-loss',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet loss percent for incoming packets (0-100). '
             'Client rx=responses; server rx=requests. Overrides --loss.'
    )
    parser.add_argument(
        '--tx-loss',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet loss percent for outgoing packets (0-100). '
             'Client tx=requests; server tx=responses. Overrides --loss.'
    )
    parser.add_argument(
        '--dup',
        type=_percent_in_range,
        default=0.0,
        metavar='<percent>',
        help='Packet duplication percent for both directions (0-100). '
             'Overridden by --rx-dup/--tx-dup.'
    )
    parser.add_argument(
        '--rx-dup',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet duplication percent for incoming packets (0-100). '
             'Client rx=responses; server rx=requests. Overrides --dup.'
    )
    parser.add_argument(
        '--tx-dup',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet duplication percent for outgoing packets (0-100). '
             'Client tx=requests; server tx=responses. Overrides --dup.'
    )
    parser.add_argument(
        '--corrupt',
        type=_percent_in_range,
        default=0.0,
        metavar='<percent>',
        help='Packet corruption percent for both directions (0-100). '
             'Corruption mutates bytes; use loss for drops. '
             'Overridden by --rx-corrupt/--tx-corrupt.'
    )
    parser.add_argument(
        '--rx-corrupt',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet corruption percent for incoming packets (0-100). '
             'Corruption mutates bytes; use loss for drops. '
             'Client rx=responses; server rx=requests. Overrides --corrupt.'
    )
    parser.add_argument(
        '--tx-corrupt',
        type=_percent_in_range,
        default=None,
        metavar='<percent>',
        help='Packet corruption percent for outgoing packets (0-100). '
             'Corruption mutates bytes; use loss for drops. '
             'Client tx=requests; server tx=responses. Overrides --corrupt.'
    )
    parser.add_argument(
        '--domain',
        required=require_domain,
        default=config.dns_base_domain,
        help='Base domain for DNS tunnel (e.g., t.example.com)'
    )
    crypto_group = parser.add_mutually_exclusive_group()
    crypto_group.add_argument(
        '--xor',
        help='Enable XOR encryption with pre-shared key'
    )
    crypto_group.add_argument(
        '--rc4',
        help='Enable RC4 encryption with pre-shared key'
    )
    crypto_group.add_argument(
        '--sha256',
        help='Enable SHA256 stream encryption with pre-shared key'
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--no-stdout-log', action='store_true',
        help='Disable stdout logging (DB logging still applies)'
    )
    parser.add_argument(
        '--db-log',
        nargs='?',
        const=_DB_LOG_DEFAULT,
        default=config.db_log_path,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--db-log-flush', type=float, default=config.db_log_flush,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--db-log-queue', type=int, default=config.db_log_queue,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--relay-buffer-size', type=int,
        default=config.relay_buffer_size,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--channel-max-send-buf', type=int,
        default=config.channel_max_send_buf,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--relay-pump-backoff-max', type=float,
        default=config.relay_pump_backoff_max,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--non-blocking-poll-timeout', type=float,
        default=config.non_blocking_poll_timeout,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--log-profile',
        default=config.log_profile,
        metavar='<log_profile>',
        help='Logging profile name (default: %s)' %
             (config.log_profile if config.log_profile else 'none')
    )
    parser.add_argument(
        '--cprofile',
        nargs='?',
        const=_CPROFILE_DEFAULT,
        default=None,
        metavar='[path]',
        help='Write cProfile output to optional path (default: /tmp/sfb_*.prof)'
    )
    parser.add_argument(
        '--tls-bump-generate-cert',
        type=int,
        metavar='<cn_length>',
        help='Generate TLS bump cert template with a fixed CN length (updates template module)'
    )


def add_dns_server_args(parser, config):
    """Add DNS server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=config.dns_listen_addr,
        help='DNS server listen host:port (IPv4 only, default: %s)' %
             config.dns_listen_addr
    )
    parser.add_argument(
        '--idle-timeout', type=int, default=config.tunnel_idle_timeout,
        help='Idle timeout in seconds (default: %s)' % config.tunnel_idle_timeout
    )


def add_dns_client_args(parser, config):
    """Add DNS client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.dns_resolver,
        help='DNS resolver host:port (IPv4 only, direct mode). Omit for system resolver '
             '(authoritative mode)'
    )


def add_icmp_common_args(parser, config):
    """Add ICMP arguments shared by client and server."""
    parser.add_argument(
        '--icmp-packet-mtu', type=int, default=config.icmp_packet_mtu,
        help='Max ICMP packet size in bytes (advanced override; leave default '
             'for auto, default: %s)' % config.icmp_packet_mtu
    )


def add_icmp_client_args(parser, config, require_target=True):
    """Add ICMP client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.icmp_target,
        required=require_target,
        help='ICMP target host or IPv4 for client'
    )


def add_udp_ephemeral_common_args(parser, config):
    """Add UDP ephemeral arguments shared by client and server."""
    parser.add_argument(
        '--udp-ephemeral-packet-mtu', type=int,
        default=config.udp_ephemeral_packet_mtu,
        help='Max UDP packet size in bytes (advanced override; leave default '
             'for auto, default: %s)' % config.udp_ephemeral_packet_mtu
    )


def add_udp_ephemeral_client_args(parser, config, require_target=True):
    """Add UDP ephemeral client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.udp_ephemeral_target,
        required=require_target,
        help='UDP target host:port for client (IPv4 only)'
    )
    parser.add_argument(
        '--udp-ephemeral-pending-timeout', type=float,
        default=config.udp_ephemeral_pending_timeout,
        help='Pending timeout in seconds (default: %s)' %
             config.udp_ephemeral_pending_timeout
    )
    parser.add_argument(
        '--udp-ephemeral-source-port-reuse-seconds', type=float,
        default=config.udp_ephemeral_source_port_reuse_seconds,
        help='Seconds before reusing a source port (default: %s)' %
             config.udp_ephemeral_source_port_reuse_seconds
    )


def add_udp_ephemeral_server_args(parser, config):
    """Add UDP ephemeral server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=None,
        help='UDP listen host:port for server (IPv4 only)'
    )


def add_tls_client_args(parser, config):
    """Add TLS client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.tls_target,
        help='TLS target host:port for client (IPv4 only)'
    )
    parser.add_argument(
        '--tls-http-proxy',
        default=config.tls_http_proxy,
        help='HTTP CONNECT proxy host:port for TLS client (IPv4 only)'
    )
    parser.add_argument(
        '--tls-http-proxy-auth',
        default=config.tls_http_proxy_auth,
        help='HTTP proxy Basic auth user:pass for TLS client (optional)'
    )
    parser.add_argument(
        '--tls-sni',
        default=config.tls_sni,
        help='TLS SNI host name (optional cover)'
    )
    parser.add_argument(
        '--tls-alpn',
        default=config.tls_alpn,
        help='TLS ALPN list (comma-separated, optional cover)'
    )
    parser.add_argument(
        '--tls-clienthello-padding-target', type=int,
        default=config.tls_clienthello_padding_target,
        help='TLS ClientHello padding target record size in bytes (0=disabled, '
             'default: %s)' % config.tls_clienthello_padding_target
    )
    parser.add_argument(
        '--tls-max-record-bytes', type=int,
        default=config.tls_max_clienthello_bytes,
        help='TLS max record size in bytes (default: %s)' %
             config.tls_max_clienthello_bytes
    )


def add_tls_server_args(parser, config):
    """Add TLS server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=None,
        help='TLS server listen host:port (IPv4 only, alias of --tls-listen-addr)'
    )
    parser.add_argument(
        '--tls-listen-addr',
        default=config.tls_listen_addr,
        help='TLS server listen host:port (IPv4 only)'
    )
    parser.add_argument(
        '--tls-sni',
        default=config.tls_sni,
        help='TLS SNI host name (optional cover, must match client)'
    )
    parser.add_argument(
        '--tls-clienthello-padding-target', type=int,
        default=config.tls_clienthello_padding_target,
        help='TLS ClientHello padding target record size in bytes (0=disabled, '
             'default: %s)' % config.tls_clienthello_padding_target
    )
    parser.add_argument(
        '--tls-max-record-bytes', type=int,
        default=config.tls_max_clienthello_bytes,
        help='TLS max record size in bytes (default: %s)' %
             config.tls_max_clienthello_bytes
    )


def add_tls_bump_client_args(parser, config):
    """Add TLS bump client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.tls_bump_target,
        help='TLS bump proxy host:port for client (IPv4 only)'
    )
    parser.add_argument(
        '--tls-bump-base-domain',
        default=config.tls_bump_base_domain,
        help='Base domain for TLS bump SNI encoding (required)'
    )
    parser.add_argument(
        '--tls-http-proxy',
        default=config.tls_bump_http_proxy,
        help='HTTP CONNECT proxy host:port for TLS bump client (IPv4 only)'
    )
    parser.add_argument(
        '--tls-http-proxy-auth',
        default=config.tls_bump_http_proxy_auth,
        help='HTTP proxy Basic auth user:pass for TLS bump client (optional)'
    )
    parser.add_argument(
        '--tls-bump-request-path',
        default=config.tls_bump_request_path,
        help='HTTPS request path to trigger proxy error page (default: %s)' %
             config.tls_bump_request_path
    )
    parser.add_argument(
        '--tls-bump-cn-max-len', type=int,
        default=config.tls_bump_cn_max_len,
        help='TLS bump CN max length override for client receive MTU'
    )


def add_tls_bump_server_args(parser, config):
    """Add TLS bump server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=None,
        help='TLS bump server listen host:port (IPv4 only, alias of --tls-bump-listen-addr)'
    )
    parser.add_argument(
        '--tls-bump-listen-addr',
        default=config.tls_bump_listen_addr,
        help='TLS bump server listen host:port (IPv4 only)'
    )
    parser.add_argument(
        '--tls-bump-base-domain',
        default=config.tls_bump_base_domain,
        help='Base domain for TLS bump SNI encoding (required)'
    )
    parser.add_argument(
        '--tls-bump-max-clienthello-bytes', type=int,
        default=config.tls_bump_max_clienthello_bytes,
        help='Max TLS ClientHello record size in bytes (default: %s)' %
             config.tls_bump_max_clienthello_bytes
    )


def add_client_pacing_args(parser, config):
    """Add transport-agnostic client pacing arguments."""
    parser.add_argument(
        '--send-rate', type=float, default=config.tunnel_send_rate,
        help='Max packets per second from Alice (0=unlimited, default: %s)' %
             config.tunnel_send_rate
    )
    parser.add_argument(
        '--send-burst', type=float, default=config.tunnel_send_burst,
        help='Burst capacity for send rate (packets, default: %s)' %
             (config.tunnel_send_burst if config.tunnel_send_burst is not None else
              'same as send_rate')
    )
    parser.add_argument(
        '--fast-retransmit', dest='fast_retransmit', action='store_true',
        default=config.tunnel_fast_retransmit_enabled,
        help='Enable fast retransmit (default: %s)' %
             config.tunnel_fast_retransmit_enabled
    )
    parser.add_argument(
        '--no-fast-retransmit', dest='fast_retransmit', action='store_false',
        help='Disable fast retransmit'
    )
    parser.add_argument(
        '--fast-retransmit-min-age-ratio', type=float,
        default=config.tunnel_fast_retransmit_min_age_ratio,
        help='Fast retransmit min age ratio of RTO (default: %s)' %
             config.tunnel_fast_retransmit_min_age_ratio
    )
    parser.add_argument(
        '--fast-retransmit-max-per-seq', type=int,
        default=config.tunnel_fast_retransmit_max_per_seq,
        help='Fast retransmit max per seq (default: %s)' %
             config.tunnel_fast_retransmit_max_per_seq
    )
    parser.add_argument(
        '--adaptive-pacing', dest='adaptive_pacing', action='store_true',
        default=config.tunnel_adaptive_pacing_enabled,
        help='Enable adaptive pacing (default: %s)' %
             config.tunnel_adaptive_pacing_enabled
    )
    parser.add_argument(
        '--no-adaptive-pacing', dest='adaptive_pacing', action='store_false',
        help='Disable adaptive pacing'
    )
    parser.add_argument(
        '--pace-target-inflight-ratio', type=float,
        default=config.tunnel_pace_target_inflight_ratio,
        help='Adaptive pacing target inflight ratio (default: %s)' %
             config.tunnel_pace_target_inflight_ratio
    )
    parser.add_argument(
        '--pace-min-inflight', type=int,
        default=config.tunnel_pace_min_inflight,
        help='Adaptive pacing minimum inflight (default: %s)' %
             config.tunnel_pace_min_inflight
    )
    parser.add_argument(
        '--pace-max-inflight', type=int,
        default=config.tunnel_pace_max_inflight,
        help='Adaptive pacing maximum inflight (default: %s)' %
             config.tunnel_pace_max_inflight
    )
    parser.add_argument(
        '--pace-feedback-gain', type=float,
        default=config.tunnel_pace_feedback_gain,
        help='Adaptive pacing feedback gain (default: %s)' %
             config.tunnel_pace_feedback_gain
    )
    parser.add_argument(
        '--pace-ack-ewma-alpha', type=float,
        default=config.tunnel_pace_ack_ewma_alpha,
        help='Adaptive pacing ACK EWMA alpha (default: %s)' %
             config.tunnel_pace_ack_ewma_alpha
    )
    parser.add_argument(
        '--pace-rtt-floor-ms', type=float,
        default=config.tunnel_pace_rtt_floor_ms,
        help='Adaptive pacing RTT floor ms (default: %s)' %
             config.tunnel_pace_rtt_floor_ms
    )
    parser.add_argument(
        '--pace-ack-idle-reset-sec', type=float,
        default=config.tunnel_pace_ack_idle_reset_sec,
        help='Adaptive pacing ACK idle reset sec (default: %s)' %
             config.tunnel_pace_ack_idle_reset_sec
    )
    parser.add_argument(
        '--poll-pacing', dest='poll_pacing', action='store_true',
        default=config.tunnel_poll_pacing_enabled,
        help='Enable poll pacing (default: %s)' %
             config.tunnel_poll_pacing_enabled
    )
    parser.add_argument(
        '--no-poll-pacing', dest='poll_pacing', action='store_false',
        help='Disable poll pacing'
    )
    parser.add_argument(
        '--poll-min-interval', type=float,
        default=config.tunnel_poll_min_interval,
        help='Poll pacing minimum interval in seconds (default: %s)' %
             config.tunnel_poll_min_interval
    )
    parser.add_argument(
        '--poll-max-interval', type=float,
        default=config.tunnel_poll_max_interval,
        help='Poll pacing maximum interval in seconds (default: %s)' %
             config.tunnel_poll_max_interval
    )
    parser.add_argument(
        '--poll-rtt-ratio', type=float,
        default=config.tunnel_poll_rtt_ratio,
        help='Poll pacing RTT ratio (default: %s)' %
             config.tunnel_poll_rtt_ratio
    )


def add_module_args(parser):
    """Add module selection argument."""
    parser.add_argument(
        '--module',
        choices=list_cli_modules(),
        help='Module to load'
    )
    parser.add_argument(
        '--module-id',
        type=_positive_int,
        default=1,
        help='Module instance id (default: 1)'
    )


def add_server_args(parser, config):
    """Add server-specific arguments."""
    parser.add_argument(
        '--root', default=config.file_transfer_root,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--max-size', type=int, default=config.file_transfer_max_size,
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--sfb-flat',
        nargs='?',
        const=_SFB_FLAT_DEFAULT,
        default=None,
        metavar='PATH',
        help='Path to sfb_flat.py (omit PATH to auto-generate)'
    )
    parser.add_argument(
        '--stager',
        nargs='?',
        const=_STAGER_DEFAULT,
        default=None,
        metavar='PATH',
        help='Path to sfb_flat.py for DNS stager packaging (omit PATH to auto-generate)'
    )
    parser.add_argument(
        '--passthrough',
        nargs=argparse.REMAINDER,
        default=None,
        metavar='ARGS',
        help='Args to pass through DNS stager to sfb_flat.py (must be last)'
    )


def _build_base_parser(config_defaults, require_domain, require_role, add_help=True):
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel',
        add_help=add_help,
    )
    add_common_args(
        parser,
        config_defaults,
        require_domain=require_domain,
        require_role=require_role,
    )
    add_module_args(parser)
    return parser


def _add_transport_args(parser, config_defaults, transport, role_for_args, generate_cert):
    if generate_cert:
        return

    def add_dns_args(parser, config_defaults, role_for_args):
        if role_for_args == 'server':
            add_dns_server_args(parser, config_defaults)
        else:
            add_dns_client_args(parser, config_defaults)

    def add_icmp_args(parser, config_defaults, role_for_args):
        add_icmp_common_args(parser, config_defaults)
        if role_for_args == 'client':
            add_icmp_client_args(parser, config_defaults, require_target=True)

    def add_udp_ephemeral_args(parser, config_defaults, role_for_args):
        add_udp_ephemeral_common_args(parser, config_defaults)
        if role_for_args == 'server':
            add_udp_ephemeral_server_args(parser, config_defaults)
        else:
            add_udp_ephemeral_client_args(
                parser, config_defaults, require_target=True
            )

    def add_tls_args(parser, config_defaults, role_for_args):
        if role_for_args == 'server':
            add_tls_server_args(parser, config_defaults)
        else:
            add_tls_client_args(parser, config_defaults)

    def add_tls_bump_args(parser, config_defaults, role_for_args):
        if role_for_args == 'server':
            add_tls_bump_server_args(parser, config_defaults)
        else:
            add_tls_bump_client_args(parser, config_defaults)

    dispatch = {
        'dns': add_dns_args,
        'icmp': add_icmp_args,
        'udp_ephemeral': add_udp_ephemeral_args,
        'tls_handshake': add_tls_args,
        'tls_handshake_bump': add_tls_bump_args,
    }
    handler = dispatch.get(transport)
    if handler:
        handler(parser, config_defaults, role_for_args)


def _add_module_commands(parser, module_cls, role_for_args, config_defaults):
    if getattr(module_cls, 'USES_SUBCOMMANDS', True):
        subparsers = parser.add_subparsers(dest='command', help='Module commands')
        module_cls.register_commands(subparsers, role_for_args, config=config_defaults)
    else:
        module_cls.register_commands(parser, role_for_args, config=config_defaults)


def parse_args(args=None):
    """
    Parse command-line arguments.

    Uses two-pass parsing:
    1. First pass gets --role, --transport, --module
    2. Second pass adds role/transport/module-specific args
    """
    if args is None:
        arg_list = sys.argv[1:]
    else:
        arg_list = list(args)
    log_profile_explicit = _has_arg_prefix(arg_list, '--log-profile')
    generate_cert = _has_arg_prefix(arg_list, '--tls-bump-generate-cert')

    # First pass: get basic options
    config_defaults = Config()
    parser = _build_base_parser(
        config_defaults,
        require_domain=False,
        require_role=False,
        add_help=False,
    )

    partial_args, remaining = parser.parse_known_args(arg_list)
    role = None
    if partial_args.role is not None:
        role = normalize_role(partial_args.role)
    transport = partial_args.transport
    role_for_args = role or 'client'

    # Second pass: full parser with role/transport/module-specific args
    parser = _build_base_parser(
        config_defaults,
        require_domain=(transport == 'dns' and not generate_cert),
        require_role=not generate_cert,
    )
    _add_transport_args(
        parser,
        config_defaults,
        transport,
        role_for_args,
        generate_cert,
    )
    if not generate_cert:
        if role_for_args == 'client':
            add_client_pacing_args(parser, config_defaults)

        # Server-specific args
        if role_for_args == 'server':
            add_server_args(parser, config_defaults)

        # Module subcommands or module-specific args
        if partial_args.module:
            module_cls = get_cli_module_class(partial_args.module)
            if module_cls is None:
                raise SystemExit('Unknown module: %s' % partial_args.module)
            _add_module_commands(parser, module_cls, role_for_args, config_defaults)

    parsed = parser.parse_args(arg_list)
    if parsed.role is not None:
        parsed.role = normalize_role(parsed.role)  # Normalize in final result
    parsed.log_profile_explicit = log_profile_explicit
    return parsed


def _build_dns_config(args):
    config_kwargs = {}
    if args.role == 'server':
        listen_addr = getattr(args, 'listen_addr', None)
        if not listen_addr:
            listen_addr = Config().dns_listen_addr
        host, port = parse_host_port(listen_addr, default_port=53)
        config_kwargs['dns_listen_addr'] = '%s:%d' % (host, port)
        config_kwargs['tunnel_idle_timeout'] = float(args.idle_timeout)
    else:
        config_kwargs['dns_resolver'] = getattr(args, 'target', None)
    return config_kwargs


def _build_icmp_config(args):
    config_kwargs = {
        'icmp_packet_mtu': getattr(args, 'icmp_packet_mtu', None),
    }
    if args.role == 'client':
        config_kwargs['icmp_target'] = getattr(args, 'target', None)
    return config_kwargs


def _build_udp_ephemeral_config(args):
    config_kwargs = {
        'udp_ephemeral_packet_mtu': getattr(
            args, 'udp_ephemeral_packet_mtu', None
        ),
    }
    if args.role == 'client':
        config_kwargs['udp_ephemeral_target'] = getattr(args, 'target', None)
        config_kwargs['udp_ephemeral_pending_timeout'] = getattr(
            args, 'udp_ephemeral_pending_timeout', None
        )
        config_kwargs['udp_ephemeral_source_port_reuse_seconds'] = getattr(
            args, 'udp_ephemeral_source_port_reuse_seconds', None
        )
    else:
        listen_addr = getattr(args, 'listen_addr', None)
        if listen_addr:
            config_kwargs['udp_ephemeral_listen_addr'] = listen_addr
    return config_kwargs


def _build_tls_handshake_config(args):
    config_kwargs = {}
    max_record_bytes = getattr(args, 'tls_max_record_bytes', None)
    if args.role == 'client':
        config_kwargs['tls_target'] = getattr(args, 'target', None)
        config_kwargs['tls_http_proxy'] = getattr(args, 'tls_http_proxy', None)
        config_kwargs['tls_http_proxy_auth'] = getattr(
            args, 'tls_http_proxy_auth', None)
        config_kwargs['tls_sni'] = getattr(args, 'tls_sni', None)
        config_kwargs['tls_alpn'] = getattr(args, 'tls_alpn', None)
        config_kwargs['tls_clienthello_padding_target'] = getattr(
            args, 'tls_clienthello_padding_target', None)
        config_kwargs['tls_max_clienthello_bytes'] = max_record_bytes
        config_kwargs['tls_max_serverhello_bytes'] = max_record_bytes
    else:
        listen_addr = getattr(args, 'listen_addr', None)
        if listen_addr:
            config_kwargs['tls_listen_addr'] = listen_addr
        else:
            config_kwargs['tls_listen_addr'] = getattr(args, 'tls_listen_addr', None)
        config_kwargs['tls_sni'] = getattr(args, 'tls_sni', None)
        config_kwargs['tls_clienthello_padding_target'] = getattr(
            args, 'tls_clienthello_padding_target', None)
        config_kwargs['tls_max_clienthello_bytes'] = max_record_bytes
        config_kwargs['tls_max_serverhello_bytes'] = max_record_bytes
    return config_kwargs


def _build_tls_bump_config(args):
    config_kwargs = {
        'tls_bump_base_domain': getattr(args, 'tls_bump_base_domain', None),
    }
    if args.role == 'client':
        config_kwargs['tls_bump_target'] = getattr(args, 'target', None)
        config_kwargs['tls_bump_http_proxy'] = getattr(args, 'tls_http_proxy', None)
        config_kwargs['tls_bump_http_proxy_auth'] = getattr(
            args, 'tls_http_proxy_auth', None)
        config_kwargs['tls_bump_request_path'] = getattr(
            args, 'tls_bump_request_path', None)
        config_kwargs['tls_bump_cn_max_len'] = getattr(
            args, 'tls_bump_cn_max_len', None)
    else:
        listen_addr = getattr(args, 'listen_addr', None)
        if listen_addr:
            config_kwargs['tls_bump_listen_addr'] = listen_addr
        else:
            config_kwargs['tls_bump_listen_addr'] = getattr(
                args, 'tls_bump_listen_addr', None)
        config_kwargs['tls_bump_max_clienthello_bytes'] = getattr(
            args, 'tls_bump_max_clienthello_bytes', None)
    return config_kwargs


def _build_client_config(args):
    return {
        'tunnel_send_rate': getattr(args, 'send_rate', None),
        'tunnel_send_burst': getattr(args, 'send_burst', None),
        'tunnel_fast_retransmit_enabled': getattr(
            args, 'fast_retransmit', None),
        'tunnel_fast_retransmit_min_age_ratio': getattr(
            args, 'fast_retransmit_min_age_ratio', None),
        'tunnel_fast_retransmit_max_per_seq': getattr(
            args, 'fast_retransmit_max_per_seq', None),
        'tunnel_adaptive_pacing_enabled': getattr(
            args, 'adaptive_pacing', None),
        'tunnel_pace_target_inflight_ratio': getattr(
            args, 'pace_target_inflight_ratio', None),
        'tunnel_pace_min_inflight': getattr(
            args, 'pace_min_inflight', None),
        'tunnel_pace_max_inflight': getattr(
            args, 'pace_max_inflight', None),
        'tunnel_pace_feedback_gain': getattr(
            args, 'pace_feedback_gain', None),
        'tunnel_pace_ack_ewma_alpha': getattr(
            args, 'pace_ack_ewma_alpha', None),
        'tunnel_pace_rtt_floor_ms': getattr(
            args, 'pace_rtt_floor_ms', None),
        'tunnel_pace_ack_idle_reset_sec': getattr(
            args, 'pace_ack_idle_reset_sec', None),
        'tunnel_poll_pacing_enabled': getattr(
            args, 'poll_pacing', None),
        'tunnel_poll_min_interval': getattr(
            args, 'poll_min_interval', None),
        'tunnel_poll_max_interval': getattr(
            args, 'poll_max_interval', None),
        'tunnel_poll_rtt_ratio': getattr(
            args, 'poll_rtt_ratio', None),
    }


def _build_server_config(args):
    return {
        'file_transfer_root': getattr(args, 'root', None),
        'file_transfer_max_size': getattr(args, 'max_size', None),
    }


def _build_logging_config(args):
    return {
        'stats_enabled': bool(getattr(args, 'verbose', False)),
        'db_log_path': getattr(args, 'db_log', None),
        'db_log_flush': getattr(args, 'db_log_flush', None),
        'db_log_queue': getattr(args, 'db_log_queue', None),
        'log_profile': getattr(args, 'log_profile', None),
        'relay_buffer_size': getattr(args, 'relay_buffer_size', None),
        'channel_max_send_buf': getattr(args, 'channel_max_send_buf', None),
        'relay_pump_backoff_max': getattr(args, 'relay_pump_backoff_max', None),
        'non_blocking_poll_timeout': getattr(args, 'non_blocking_poll_timeout', None),
    }


def _build_crypto_config(args):
    config_kwargs = {}
    if getattr(args, 'xor', None) is not None:
        config_kwargs['crypto_mode'] = 'xor'
        config_kwargs['crypto_psk'] = _normalize_psk(args.xor)
    elif getattr(args, 'rc4', None) is not None:
        config_kwargs['crypto_mode'] = 'rc4'
        config_kwargs['crypto_psk'] = _normalize_psk(args.rc4)
    elif getattr(args, 'sha256', None) is not None:
        config_kwargs['crypto_mode'] = 'sha256'
        config_kwargs['crypto_psk'] = _normalize_psk(args.sha256)
    return config_kwargs


def create_config(args):
    """Create Config from parsed arguments."""
    config_kwargs = {
        'dns_base_domain': args.domain,
        'transport': args.transport,
        'max_in_flight': getattr(args, 'max_in_flight', None),
    }
    transport_builders = {
        'dns': _build_dns_config,
        'icmp': _build_icmp_config,
        'udp_ephemeral': _build_udp_ephemeral_config,
        'tls_handshake': _build_tls_handshake_config,
        'tls_handshake_bump': _build_tls_bump_config,
    }
    transport_builder = transport_builders.get(args.transport)
    if transport_builder:
        config_kwargs.update(transport_builder(args))

    if args.role == 'client':
        config_kwargs.update(_build_client_config(args))
    elif args.role == 'server':
        config_kwargs.update(_build_server_config(args))

    config_kwargs.update(_build_logging_config(args))
    config_kwargs.update(_build_crypto_config(args))

    config_kwargs = {k: v for k, v in config_kwargs.items() if v is not None}
    return Config(**config_kwargs)


def _normalize_psk(psk):
    if psk is None:
        return None
    if isinstance(psk, text_type):
        return psk.encode('utf-8')
    return psk


def create_crypto(args, logger):
    """Create crypto instance from args."""
    if args.xor is not None:
        crypto = XOR(_normalize_psk(args.xor))
        log_event(
            logger,
            logging.INFO,
            'cli.crypto',
            'Encryption enabled',
            lambda: {'mode': 'xor'},
        )
    elif args.rc4 is not None:
        crypto = RC4(_normalize_psk(args.rc4))
        log_event(
            logger,
            logging.INFO,
            'cli.crypto',
            'Encryption enabled',
            lambda: {'mode': 'rc4'},
        )
    elif args.sha256 is not None:
        crypto = SHA256(_normalize_psk(args.sha256))
        log_event(
            logger,
            logging.INFO,
            'cli.crypto',
            'Encryption enabled',
            lambda: {'mode': 'sha256'},
        )
    else:
        crypto = Plain()
        log_event(
            logger,
            logging.INFO,
            'cli.crypto',
            'Encryption disabled',
            lambda: {'mode': 'none'},
        )
    return crypto


def _resolve_directional_percents(base, tx_override, rx_override):
    base = base or 0.0
    tx_percent = base
    rx_percent = base
    if tx_override is not None:
        tx_percent = tx_override
    if rx_override is not None:
        rx_percent = rx_override
    return tx_percent, rx_percent


def _resolve_loss_percents(args):
    return _resolve_directional_percents(
        getattr(args, 'loss', 0.0),
        getattr(args, 'tx_loss', None),
        getattr(args, 'rx_loss', None),
    )


def _resolve_dup_percents(args):
    return _resolve_directional_percents(
        getattr(args, 'dup', 0.0),
        getattr(args, 'tx_dup', None),
        getattr(args, 'rx_dup', None),
    )


def _resolve_corrupt_percents(args):
    return _resolve_directional_percents(
        getattr(args, 'corrupt', 0.0),
        getattr(args, 'tx_corrupt', None),
        getattr(args, 'rx_corrupt', None),
    )


def _percent_pair_to_rates(tx_percent, rx_percent):
    return tx_percent / 100.0, rx_percent / 100.0


def _build_lossy_impairments(
        impairment_cls,
        tx_loss_rate,
        rx_loss_rate,
        tx_dup_rate,
        rx_dup_rate,
        tx_corrupt_rate,
        rx_corrupt_rate,
):
    if (tx_loss_rate == rx_loss_rate and
            tx_dup_rate == rx_dup_rate and
            tx_corrupt_rate == rx_corrupt_rate):
        impairment = impairment_cls(
            loss_rate=tx_loss_rate,
            dup_rate=tx_dup_rate,
            corrupt_rate=tx_corrupt_rate,
        )
        return impairment, impairment
    send_impairment = impairment_cls(
        loss_rate=tx_loss_rate,
        dup_rate=tx_dup_rate,
        corrupt_rate=tx_corrupt_rate,
    )
    recv_impairment = impairment_cls(
        loss_rate=rx_loss_rate,
        dup_rate=rx_dup_rate,
        corrupt_rate=rx_corrupt_rate,
    )
    return send_impairment, recv_impairment


def _wrap_lossy_transport(transport, args, role, logger):
    tx_loss_percent, rx_loss_percent = _resolve_loss_percents(args)
    tx_dup_percent, rx_dup_percent = _resolve_dup_percents(args)
    tx_corrupt_percent, rx_corrupt_percent = _resolve_corrupt_percents(args)
    if (tx_loss_percent <= 0 and rx_loss_percent <= 0 and
            tx_dup_percent <= 0 and rx_dup_percent <= 0 and
            tx_corrupt_percent <= 0 and rx_corrupt_percent <= 0):
        return transport
    try:
        impairment_cls, lossy_transport_cls, lossy_server_cls = load_lossy()
    except ImportError:
        raise TransportError('Lossy transport is not available in this build')
    tx_loss_rate, rx_loss_rate = _percent_pair_to_rates(
        tx_loss_percent, rx_loss_percent
    )
    tx_dup_rate, rx_dup_rate = _percent_pair_to_rates(
        tx_dup_percent, rx_dup_percent
    )
    tx_corrupt_rate, rx_corrupt_rate = _percent_pair_to_rates(
        tx_corrupt_percent, rx_corrupt_percent
    )
    send_impairment, recv_impairment = _build_lossy_impairments(
        impairment_cls,
        tx_loss_rate,
        rx_loss_rate,
        tx_dup_rate,
        rx_dup_rate,
        tx_corrupt_rate,
        rx_corrupt_rate,
    )
    stats_enabled = bool(args.verbose)
    if role == 'client':
        wrapped = lossy_transport_cls(
            transport,
            send_impairment=send_impairment,
            recv_impairment=recv_impairment,
            stats_enabled=stats_enabled,
        )
    else:
        wrapped = lossy_server_cls(
            transport,
            recv_impairment=recv_impairment,
            send_impairment=send_impairment,
            stats_enabled=stats_enabled,
        )
    log_event(
        logger,
        logging.INFO,
        'cli.lossy_transport',
        'Lossy transport enabled',
        lambda: {
            'role': role,
            'transport': args.transport,
            'tx_loss_percent': tx_loss_percent,
            'rx_loss_percent': rx_loss_percent,
            'tx_loss_rate': tx_loss_rate,
            'rx_loss_rate': rx_loss_rate,
            'tx_dup_percent': tx_dup_percent,
            'rx_dup_percent': rx_dup_percent,
            'tx_dup_rate': tx_dup_rate,
            'rx_dup_rate': rx_dup_rate,
            'tx_corrupt_percent': tx_corrupt_percent,
            'rx_corrupt_percent': rx_corrupt_percent,
            'tx_corrupt_rate': tx_corrupt_rate,
            'rx_corrupt_rate': rx_corrupt_rate,
        },
    )
    return wrapped


def run_server(args, config, crypto, logger):
    """Run in server role."""
    # Change to root directory for file transfers
    root = os.path.abspath(config.file_transfer_root)
    if not os.path.isdir(root):
        log_event(
            logger,
            logging.ERROR,
            'cli.root_missing',
            'Root directory does not exist',
            lambda: {'path': root},
        )
        return 1
    os.chdir(root)
    log_event(
        logger,
        logging.INFO,
        'cli.working_dir',
        'Working directory',
        lambda: {'path': root},
    )

    # Create transport and tunnel
    try:
        from .tunnel.bob_tunnel import BobTunnel
        transport_cls = get_transport_class(args.transport, 'server')
        transport = transport_cls(config)
        transport = _wrap_lossy_transport(transport, args, 'server', logger)
        tunnel = BobTunnel(transport, config, crypto=crypto)
    except (TransportError, TunnelError) as e:
        _print_error(str(e))
        return 1

    # Signal handling
    shutdown_requested = [False]

    def handle_signal(sig, frame):
        if shutdown_requested[0]:
            # Force exit without raising SystemExit during atexit cleanup.
            os._exit(1)
        shutdown_requested[0] = True
        log_event(
            logger,
            logging.INFO,
            'cli.shutdown',
            'Shutting down',
            lambda: None,
        )
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Run module if provided, otherwise passive serve
    if args.module:
        return run_server_command(args, tunnel, logger, shutdown_requested)
    else:
        return run_server_passive(args, tunnel, logger)


def run_server_passive(args, tunnel, logger):
    """Run server in passive mode (no command, just wait for connections)."""
    if args.transport == 'dns':
        host, port = parse_host_port(tunnel._config.dns_listen_addr, default_port=53)
        log_event(
            logger,
            logging.INFO,
            'cli.listen',
            'Listening (passive mode)',
            lambda: {'transport': 'dns', 'host': host, 'port': port, 'domain': args.domain},
        )
    elif args.transport == 'icmp':
        log_event(
            logger,
            logging.INFO,
            'cli.listen',
            'Listening (passive mode)',
            lambda: {'transport': 'icmp'},
        )
    try:
        tunnel.serve_forever()
    except Exception as e:
        log_event(
            logger,
            logging.ERROR,
            'cli.serve_error',
            'Error in serve loop',
            lambda: {'error': str(e)},
        )
        log_event(
            logger,
            logging.ERROR,
            'cli.traceback',
            'Serve loop traceback',
            lambda: {'context': 'serve_loop'},
            exc_info=True,
        )
        return 1
    finally:
        tunnel.close()
        log_event(
            logger,
            logging.INFO,
            'cli.shutdown_complete',
            'Shutdown complete',
            lambda: None,
        )
    return 0


def _wait_for_client(tunnel, args, logger, shutdown_requested):
    if args.transport == 'dns':
        host, port = parse_host_port(tunnel._config.dns_listen_addr, default_port=53)
        log_event(
            logger,
            logging.INFO,
            'cli.wait_client',
            'Waiting for client',
            lambda: {'transport': 'dns', 'host': host, 'port': port},
        )
    elif args.transport == 'icmp':
        log_event(
            logger,
            logging.INFO,
            'cli.wait_client',
            'Waiting for client',
            lambda: {'transport': 'icmp'},
        )
    while tunnel._state != TunnelState.CONNECTED:
        if shutdown_requested[0]:
            return False
        time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)

    log_event(
        logger,
        logging.INFO,
        'cli.client_connected',
        'Client connected',
        lambda: None,
    )
    return True


def _load_remote_module(tunnel, args, logger, module_loader):
    module_name = args.module
    module_id = args.module_id
    module_cls = get_cli_module_class(module_name)
    if module_cls is None:
        raise ModuleError('invalid_module', 'unknown module: %s' % module_name)
    module_logger = get_logger('sfb.modules.%s' % module_name)
    remote_module = module_cls.REMOTE_MODULE or module_name
    log_event(
        logger,
        logging.INFO,
        'cli.module_load',
        'Loading module on peer',
        lambda: {'module': remote_module, 'mid': module_id},
    )
    module_loader.load_remote(remote_module, module_id)
    log_event(
        logger,
        logging.INFO,
        'cli.module_loaded',
        'Module loaded (module=%s)' % remote_module,
        lambda: {'module': remote_module, 'mid': module_id},
    )
    return module_cls, module_logger


def _resolve_module_command(args, module_cls, logger):
    if getattr(module_cls, 'USES_SUBCOMMANDS', True):
        if getattr(args, 'command', None) is None:
            default_cmd = getattr(module_cls, 'DEFAULT_COMMAND', None)
            if default_cmd:
                args.command = default_cmd
            elif getattr(module_cls, 'REQUIRES_COMMAND', False):
                log_event(
                    logger,
                    logging.ERROR,
                    'cli.module_command_required',
                    'Module requires a command',
                    lambda: {'module': args.module, 'mid': args.module_id},
                )
                return False
    return True


def _unload_remote_module(tunnel, args, module_loader):
    module_name = args.module
    module_id = args.module_id
    module_cls = get_cli_module_class(module_name)
    if module_cls is None:
        return
    remote_module = module_cls.REMOTE_MODULE or module_name
    try:
        module_loader.unload_remote(remote_module, module_id)
    except ModuleLoadError:
        pass


def run_server_command(args, tunnel, logger, shutdown_requested):
    """Run server in command mode - wait for client, load module, execute."""
    try:
        module_loader = tunnel.enable_module_loader(logger=logger)

        # Start background serve loop
        tunnel.start_background()

        # Wait for client to connect
        if not _wait_for_client(tunnel, args, logger, shutdown_requested):
            return 1

        module_cls, module_logger = _load_remote_module(
            tunnel, args, logger, module_loader
        )

        # Allow module message type
        tunnel.allow_message_type(module_cls.TYPE)

        if not _resolve_module_command(args, module_cls, logger):
            return 1

        # Run module command
        exit_code = module_cls.run_command(args, tunnel, module_logger)
        if tunnel._state == TunnelState.CONNECTED:
            _unload_remote_module(tunnel, args, module_loader)
        return exit_code

    except ModuleError as e:
        module_label = getattr(args, 'module', None) or 'module'
        reason = e.reason or str(e) or e.code
        _print_error('%s error: %s' % (module_label, reason))
        log_event(
            logger,
            logging.ERROR,
            'cli.module_error',
            'Module error',
            lambda: {
                'module': module_label,
                'mid': getattr(args, 'module_id', None),
                'code': e.code,
                'reason': reason,
            },
        )
        return 1
    except Exception as e:
        log_event(
            logger,
            logging.ERROR,
            'cli.error',
            'Error',
            lambda: {'error': str(e)},
        )
        if args.verbose:
            log_event(
                logger,
                logging.ERROR,
                'cli.traceback',
                'Full traceback',
                lambda: {'context': 'server_command'},
                exc_info=True,
            )
        return 1

    finally:
        tunnel.close()
        log_event(
            logger,
            logging.INFO,
            'cli.shutdown_complete',
            'Shutdown complete',
            lambda: None,
        )


def run_client(args, config, crypto, logger):
    """Run in client role."""
    # Create transport and tunnel
    try:
        from .tunnel.alice_tunnel import AliceTunnel
        transport_cls = get_transport_class(args.transport, 'client')
        transport = transport_cls(config)
        transport = _wrap_lossy_transport(transport, args, 'client', logger)
        tunnel = AliceTunnel(transport, config, crypto=crypto)
    except (TransportError, TunnelError) as e:
        _print_error(str(e))
        return 1

    # Signal handling
    shutdown_requested = [False]

    def handle_signal(sig, frame):
        if shutdown_requested[0]:
            # Force exit without raising SystemExit during atexit cleanup.
            os._exit(1)
        shutdown_requested[0] = True
        log_event(
            logger,
            logging.INFO,
            'cli.shutdown',
            'Shutting down',
            lambda: None,
        )
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Connect
        if args.transport == 'dns':
            resolver_desc = getattr(args, 'target', None) or 'system resolver'
            log_event(
                logger,
                logging.INFO,
                'cli.connect',
                'Connecting',
                lambda: {'transport': 'dns', 'domain': args.domain, 'resolver': resolver_desc},
            )
        elif args.transport == 'icmp':
            target = getattr(args, 'target', None)
            log_event(
                logger,
                logging.INFO,
                'cli.connect',
                'Connecting',
                lambda: {'transport': 'icmp', 'target': target},
            )
        tunnel.connect()
        log_event(
            logger,
            logging.INFO,
            'cli.connected',
            'Connected',
            lambda: None,
        )

        # Start background tick loop
        tunnel.start_background()

        log_event(
            logger,
            logging.INFO,
            'cli.wait_commands',
            'Waiting for commands',
            lambda: None,
        )

        # Run until connection closes or signal received
        while tunnel._state == TunnelState.CONNECTED and not shutdown_requested[0]:
            time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)

        return 0

    except Exception as e:
        log_event(
            logger,
            logging.ERROR,
            'cli.error',
            'Error',
            lambda: {'error': str(e)},
        )
        if args.verbose:
            log_event(
                logger,
                logging.ERROR,
                'cli.traceback',
                'Full traceback',
                lambda: {'context': 'client'},
                exc_info=True,
            )
        return 1

    finally:
        tunnel.close()
        log_event(
            logger,
            logging.INFO,
            'cli.shutdown_complete',
            'Shutdown complete',
            lambda: None,
        )


def _ensure_db_log_path(parsed):
    if not parsed.db_log:
        return
    db_dir = os.path.dirname(parsed.db_log)
    if os.path.exists(parsed.db_log):
        if os.path.isfile(parsed.db_log):
            try:
                os.remove(parsed.db_log)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise
        else:
            raise OSError(errno.EEXIST, 'db log path is not a file', parsed.db_log)
    if db_dir:
        try:
            os.makedirs(db_dir)
        except OSError as e:
            if e.errno != errno.EEXIST or not os.path.isdir(db_dir):
                raise


def _configure_root_logging(parsed, cprofile_path):
    level = logging.DEBUG if parsed.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s %(message)s'
    )
    root_logger = logging.getLogger()
    if parsed.no_stdout_log:
        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.StreamHandler):
                root_logger.removeHandler(handler)
    else:
        stdout_formatter = StructuredLogFormatter(
            '%(message)s',
            max_line_length=160,
            include_message=False,
            label_event=False,
            label_fields=False,
        )
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setFormatter(stdout_formatter)
    if parsed.db_log:
        _ensure_db_log_path(parsed)
        formatter = logging.Formatter('%(name)s %(levelname)s %(message)s')
        add_sqlite_handler(
            root_logger,
            parsed.db_log,
            level=level,
            formatter=formatter,
            flush_interval=parsed.db_log_flush,
            queue_maxsize=parsed.db_log_queue,
        )
    return root_logger


def _log_startup(logger, parsed, cprofile_path, config):
    log_event(
        logger,
        logging.INFO,
        'cli.log_startup',
        'Log configuration snapshot',
        lambda: {
            'role': parsed.role,
            'transport': parsed.transport,
            'log_profile': parsed.log_profile,
            'log_profile_explicit': bool(
                getattr(parsed, 'log_profile_explicit', False)
            ),
            'cprofile_path': cprofile_path,
            'db_log_path': parsed.db_log,
            'db_log_flush': parsed.db_log_flush,
            'db_log_queue': parsed.db_log_queue,
            'stdout_logging': not parsed.no_stdout_log,
            'cwd': os.getcwd(),
            'log_event_whitelist': config.log_event_whitelist,
            'log_event_blacklist': config.log_event_blacklist,
            'log_component_transport_dns': config.log_component_transport_dns,
            'log_component_transport_icmp': config.log_component_transport_icmp,
            'log_component_transport_tls': config.log_component_transport_tls,
            'log_component_tunnel': config.log_component_tunnel,
            'log_component_channel': config.log_component_channel,
            'log_component_protocol': config.log_component_protocol,
            'log_component_module_relay': config.log_component_module_relay,
            'log_component_module_file_transfer': (
                config.log_component_module_file_transfer
            ),
            'log_component_module_nc_linux': config.log_component_module_nc_linux,
        },
    )
    if cprofile_path:
        log_event(
            logger,
            logging.INFO,
            'cli.cprofile',
            'cProfile enabled',
            lambda: {'path': cprofile_path, 'mode': 'threads'},
        )


def _prepare_sfb_flat(parsed):
    flat_value = getattr(parsed, 'sfb_flat', None)
    if flat_value is None:
        return 0
    if parsed.role != 'server':
        _print_error('--sfb-flat requires --role server')
        return 2
    if flat_value is _SFB_FLAT_DEFAULT:
        flat_path = _auto_flatten_sfb_flat(parsed.transport)
        if flat_path is None:
            return 2
    else:
        flat_path = os.path.abspath(flat_value)
        if not os.path.isfile(flat_path):
            _print_error('sfb flat path not found: %s' % flat_path)
            return 2
    parsed.sfb_flat = flat_path
    return 0


def _prepare_dns_stager(parsed, config):
    stager_value = getattr(parsed, 'stager', None)
    passthrough = getattr(parsed, 'passthrough', None)
    if stager_value is None:
        if passthrough:
            _print_error('--passthrough requires --stager')
            return 2
        return 0
    if parsed.role != 'server':
        _print_error('--stager requires --role server')
        return 2
    if parsed.transport != 'dns':
        _print_error('--stager requires --transport dns')
        return 2
    if stager_value is _STAGER_DEFAULT:
        stager_path = _auto_flatten_sfb_flat(parsed.transport)
        if stager_path is None:
            return 2
    else:
        stager_path = os.path.abspath(stager_value)
        if not os.path.isfile(stager_path):
            _print_error('stager path not found: %s' % stager_path)
            return 2
    try:
        with open(stager_path, 'rb') as handle:
            payload = handle.read()
    except (IOError, OSError) as exc:
        _print_error('Failed to read stager path: %s' % exc)
        return 2
    if not payload:
        _print_error('stager path is empty: %s' % stager_path)
        return 2
    try:
        payload_cap = _calc_flat_payload_cap(
            config.dns_base_domain,
            config.dns_cname_label,
            config.dns_label_max_len,
        )
    except ValueError as exc:
        _print_error('Stager chunk size error: %s' % exc)
        return 2
    gz_payload = _gzip_bytes(payload)
    if not gz_payload:
        _print_error('stager gzip payload empty')
        return 2
    chunks = _split_chunks(gz_payload, payload_cap)
    if not chunks:
        _print_error('stager payload chunking failed')
        return 2
    count = len(chunks)
    meta = struct.pack('>2sBI', b'SF', 1, count)
    config.dns_flat_chunks = chunks
    config.dns_flat_count = count
    config.dns_flat_meta = meta
    config.dns_flat_chunk_size = payload_cap
    try:
        from .stagers import dns_stager
        dns_stager.write_dns_stagers(
            config.dns_base_domain,
            sfb_args=passthrough or [],
            cname_label=config.dns_cname_label,
        )
    except (IOError, OSError, ValueError) as exc:
        _print_error('Failed to generate DNS stagers: %s' % exc)
        return 2
    return 0


def _run_main(parsed, cprofile_path):
    """Run the CLI with parsed args."""
    cert_result = _handle_tls_bump_generate_cert(parsed)
    if cert_result is not None:
        return cert_result
    if parsed.db_log is _DB_LOG_DEFAULT:
        # --db-log passed without a path, use default
        parsed.db_log = './logs/%s_log.db' % parsed.role
    if getattr(parsed, 'log_profile_explicit', False):
        parsed.verbose = True

    flat_result = _prepare_sfb_flat(parsed)
    if flat_result:
        return flat_result

    config = create_config(parsed)
    stager_result = _prepare_dns_stager(parsed, config)
    if stager_result:
        return stager_result
    if parsed.log_profile:
        try:
            from .log_profiles import apply_log_profile
        except ImportError:
            _print_error('Log profiles not available in this build')
            return 2
        try:
            apply_log_profile(config, parsed.log_profile)
        except ValueError as e:
            _print_error(str(e))
            return 2
    if parsed.verbose and config.tunnel_pacer_summary_interval <= 0:
        config.tunnel_pacer_summary_interval = 1.0

    # Setup logging
    root_logger = _configure_root_logging(parsed, cprofile_path)
    add_component_filters(root_logger, config)
    logger = logging.getLogger('sfb')
    _log_startup(logger, parsed, cprofile_path, config)

    # Create config and crypto
    crypto = create_crypto(parsed, logger)

    # Dispatch to role
    if parsed.role == 'server':
        return run_server(parsed, config, crypto, logger)
    else:
        return run_client(parsed, config, crypto, logger)


def main(args=None):
    """Main entry point."""
    parsed = parse_args(args)
    cprofile_path = _resolve_cprofile_path(
        getattr(parsed, 'cprofile', None),
        parsed.role,
        parsed.transport,
    )
    profiler = None
    if cprofile_path:
        try:
            from .profiling import CProfileManager
        except ImportError:
            _print_error('cProfile support not available in this build')
            return 2
        try:
            _ensure_parent_dir(cprofile_path)
        except OSError as e:
            _print_error(
                'Failed to prepare cProfile output path %s: %s' %
                (cprofile_path, e)
            )
            return 2
        profiler = CProfileManager()
        try:
            profiler.start()
        except Exception as e:
            _print_error(
                'Failed to start cProfile: %s' % e
            )
            return 2
    try:
        return _run_main(parsed, cprofile_path)
    finally:
        if profiler is not None:
            try:
                profiler.stop()
                profiler.dump_stats(cprofile_path)
            except Exception as e:
                _print_error(
                    'Failed to write cProfile output to %s: %s' %
                    (cprofile_path, e)
                )


if __name__ == '__main__':
    sys.exit(main())
