# -*- coding: ascii -*-
"""
Generic CLI for sfb tunnel.

Provides a unified entry point supporting:
- Roles: server (bob) or client (alice)
- Transports: dns (extensible)
- Modules: file_transfer, socks_server, socks_relay, port_fwd_server, port_fwd_relay, etc.
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

from .config import Config
from .compat import byte_at, text_type
from .crypto import Plain, RC4, XOR
from .logging_util import add_component_filters, add_sqlite_handler, get_logger, log_event
from .log_profiles import LOG_PROFILES, apply_log_profile
from .transport import TRANSPORTS, TransportError, get_transport_class
from .tunnel import AliceTunnel, BobTunnel, TunnelState
from .modules import AVAILABLE_MODULES
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


def _print_error(message):
    prefix = 'ERROR: '
    if sys.stderr.isatty():
        sys.stderr.write('\x1b[31m' + prefix + message + '\x1b[0m\n')
    else:
        sys.stderr.write(prefix + message + '\n')
    sys.stderr.flush()


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
        choices=list(TRANSPORTS.keys()),
        help='Transport type (default: %s)' % config.transport_default
    )
    parser.add_argument(
        '--max-in-flight',
        dest='max_in_flight', type=int, default=config.max_in_flight,
        help='Max in-flight packets (1-256, default: %s)' %
             config.max_in_flight
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
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Enable debug logging'
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
        help='Logging profile name (default: %s)' % config.log_profile
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
        help='DNS server listen host:port (default: %s)' %
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
        help='DNS resolver host:port (direct mode). Omit for system resolver (authoritative mode)'
    )


def add_icmp_common_args(parser, config):
    """Add ICMP arguments shared by client and server."""
    parser.add_argument(
        '--icmp-mtu', type=int, default=config.icmp_payload_mtu,
        help='Max ICMP payload size in bytes (default: %s)' %
             config.icmp_payload_mtu
    )


def add_icmp_client_args(parser, config, require_target=True):
    """Add ICMP client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.icmp_target,
        required=require_target,
        help='ICMP target host or IP for client'
    )


def add_udp_ephemeral_common_args(parser, config):
    """Add UDP ephemeral arguments shared by client and server."""
    parser.add_argument(
        '--udp-ephemeral-mtu', type=int,
        default=config.udp_ephemeral_payload_mtu,
        help='Max UDP payload size in bytes (default: %s)' %
             config.udp_ephemeral_payload_mtu
    )


def add_udp_ephemeral_client_args(parser, config, require_target=True):
    """Add UDP ephemeral client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.udp_ephemeral_target,
        required=require_target,
        help='UDP target host:port for client'
    )
    parser.add_argument(
        '--udp-ephemeral-pending-timeout', type=float,
        default=config.udp_ephemeral_pending_timeout,
        help='Pending timeout in seconds (default: %s)' %
             config.udp_ephemeral_pending_timeout
    )
    parser.add_argument(
        '--udp-ephemeral-source-port-reuse-minutes', type=float,
        default=config.udp_ephemeral_source_port_reuse_minutes,
        help='Minutes before reusing a source port (default: %s)' %
             config.udp_ephemeral_source_port_reuse_minutes
    )


def add_udp_ephemeral_server_args(parser, config):
    """Add UDP ephemeral server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=None,
        help='UDP listen host:port for server'
    )


def add_tls_client_args(parser, config):
    """Add TLS client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.tls_target,
        help='TLS target host:port for client'
    )
    parser.add_argument(
        '--tls-http-proxy',
        default=config.tls_http_proxy,
        help='HTTP CONNECT proxy host:port for TLS client'
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
        '--tls-mtu', type=int,
        default=config.tls_max_clienthello_bytes,
        help='TLS max record size in bytes (default: %s)' %
             config.tls_max_clienthello_bytes
    )


def add_tls_server_args(parser, config):
    """Add TLS server-specific arguments."""
    parser.add_argument(
        '--listen-addr',
        default=None,
        help='TLS server listen host:port (alias of --tls-listen-addr)'
    )
    parser.add_argument(
        '--tls-listen-addr',
        default=config.tls_listen_addr,
        help='TLS server listen host:port'
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
        '--tls-mtu', type=int,
        default=config.tls_max_clienthello_bytes,
        help='TLS max record size in bytes (default: %s)' %
             config.tls_max_clienthello_bytes
    )


def add_tls_bump_client_args(parser, config):
    """Add TLS bump client-specific arguments."""
    parser.add_argument(
        '--target',
        default=config.tls_bump_target,
        help='TLS bump proxy host:port for client'
    )
    parser.add_argument(
        '--tls-bump-base-domain',
        default=config.tls_bump_base_domain,
        help='Base domain for TLS bump SNI encoding (required)'
    )
    parser.add_argument(
        '--tls-http-proxy',
        default=config.tls_bump_http_proxy,
        help='HTTP CONNECT proxy host:port for TLS bump client'
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
        help='TLS bump server listen host:port (alias of --tls-bump-listen-addr)'
    )
    parser.add_argument(
        '--tls-bump-listen-addr',
        default=config.tls_bump_listen_addr,
        help='TLS bump server listen host:port'
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
        choices=list(AVAILABLE_MODULES.keys()),
        help='Module to load'
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
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel',
        add_help=False,  # Add help in second pass
    )
    config_defaults = Config()
    add_common_args(
        parser,
        config_defaults,
        require_domain=False,
        require_role=False,
    )
    add_module_args(parser)

    partial_args, remaining = parser.parse_known_args(arg_list)
    role = None
    if partial_args.role is not None:
        role = normalize_role(partial_args.role)
    transport = partial_args.transport
    role_for_args = role or 'client'

    # Second pass: full parser with role/transport/module-specific args
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel'
    )
    add_common_args(
        parser,
        config_defaults,
        require_domain=(transport == 'dns' and not generate_cert),
        require_role=not generate_cert,
    )
    add_module_args(parser)

    if not generate_cert:
        # Transport-specific args
        if transport == 'dns':
            if role_for_args == 'server':
                add_dns_server_args(parser, config_defaults)
            else:
                add_dns_client_args(parser, config_defaults)
        elif transport == 'icmp':
            add_icmp_common_args(parser, config_defaults)
            if role_for_args == 'client':
                add_icmp_client_args(parser, config_defaults, require_target=True)
        elif transport == 'udp_ephemeral':
            add_udp_ephemeral_common_args(parser, config_defaults)
            if role_for_args == 'server':
                add_udp_ephemeral_server_args(parser, config_defaults)
            else:
                add_udp_ephemeral_client_args(
                    parser, config_defaults, require_target=True
                )
        elif transport == 'tls_handshake':
            if role_for_args == 'server':
                add_tls_server_args(parser, config_defaults)
            else:
                add_tls_client_args(parser, config_defaults)
        elif transport == 'tls_handshake_bump':
            if role_for_args == 'server':
                add_tls_bump_server_args(parser, config_defaults)
            else:
                add_tls_bump_client_args(parser, config_defaults)
        if role_for_args == 'client':
            add_client_pacing_args(parser, config_defaults)

        # Server-specific args
        if role_for_args == 'server':
            add_server_args(parser, config_defaults)

        # Module subcommands or module-specific args
        if partial_args.module:
            module_cls = AVAILABLE_MODULES[partial_args.module]
            if getattr(module_cls, 'USES_SUBCOMMANDS', True):
                subparsers = parser.add_subparsers(dest='command', help='Module commands')
                module_cls.register_commands(subparsers, role_for_args, config=config_defaults)
            else:
                module_cls.register_commands(parser, role_for_args, config=config_defaults)

    parsed = parser.parse_args(arg_list)
    if parsed.role is not None:
        parsed.role = normalize_role(parsed.role)  # Normalize in final result
    parsed.log_profile_explicit = log_profile_explicit
    return parsed


def create_config(args):
    """Create Config from parsed arguments."""
    config_kwargs = {
        'dns_base_domain': args.domain,
        'transport': args.transport,
    }
    config_kwargs['max_in_flight'] = getattr(args, 'max_in_flight', None)

    # DNS transport args
    if args.transport == 'dns':
        if args.role == 'server':
            listen_addr = getattr(args, 'listen_addr', None)
            if not listen_addr:
                listen_addr = Config().dns_listen_addr
            host, port = parse_host_port(listen_addr, default_port=53)
            config_kwargs['dns_listen_addr'] = '%s:%d' % (host, port)
            config_kwargs['tunnel_idle_timeout'] = float(args.idle_timeout)
        else:
            config_kwargs['dns_resolver'] = getattr(args, 'target', None)
    elif args.transport == 'icmp':
        config_kwargs['icmp_payload_mtu'] = getattr(args, 'icmp_mtu', None)
        if args.role == 'client':
            config_kwargs['icmp_target'] = getattr(args, 'target', None)
    elif args.transport == 'udp_ephemeral':
        config_kwargs['udp_ephemeral_payload_mtu'] = getattr(
            args, 'udp_ephemeral_mtu', None
        )
        if args.role == 'client':
            config_kwargs['udp_ephemeral_target'] = getattr(args, 'target', None)
            config_kwargs['udp_ephemeral_pending_timeout'] = getattr(
                args, 'udp_ephemeral_pending_timeout', None
            )
            config_kwargs['udp_ephemeral_source_port_reuse_minutes'] = getattr(
                args, 'udp_ephemeral_source_port_reuse_minutes', None
            )
        else:
            listen_addr = getattr(args, 'listen_addr', None)
            if listen_addr:
                config_kwargs['udp_ephemeral_listen_addr'] = listen_addr
    elif args.transport == 'tls_handshake':
        if args.role == 'client':
            config_kwargs['tls_target'] = getattr(args, 'target', None)
            config_kwargs['tls_http_proxy'] = getattr(args, 'tls_http_proxy', None)
            config_kwargs['tls_http_proxy_auth'] = getattr(args, 'tls_http_proxy_auth', None)
            config_kwargs['tls_sni'] = getattr(args, 'tls_sni', None)
            config_kwargs['tls_alpn'] = getattr(args, 'tls_alpn', None)
            config_kwargs['tls_clienthello_padding_target'] = getattr(
                args, 'tls_clienthello_padding_target', None)
            config_kwargs['tls_max_clienthello_bytes'] = getattr(args, 'tls_mtu', None)
            config_kwargs['tls_max_serverhello_bytes'] = getattr(args, 'tls_mtu', None)
        else:
            listen_addr = getattr(args, 'listen_addr', None)
            if listen_addr:
                config_kwargs['tls_listen_addr'] = listen_addr
            else:
                config_kwargs['tls_listen_addr'] = getattr(args, 'tls_listen_addr', None)
            config_kwargs['tls_sni'] = getattr(args, 'tls_sni', None)
            config_kwargs['tls_clienthello_padding_target'] = getattr(
                args, 'tls_clienthello_padding_target', None)
            config_kwargs['tls_max_clienthello_bytes'] = getattr(args, 'tls_mtu', None)
            config_kwargs['tls_max_serverhello_bytes'] = getattr(args, 'tls_mtu', None)
    elif args.transport == 'tls_handshake_bump':
        config_kwargs['tls_bump_base_domain'] = getattr(args, 'tls_bump_base_domain', None)
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

    if args.role == 'client':
        config_kwargs['tunnel_send_rate'] = getattr(args, 'send_rate', None)
        config_kwargs['tunnel_send_burst'] = getattr(args, 'send_burst', None)
        config_kwargs['tunnel_fast_retransmit_enabled'] = getattr(
            args, 'fast_retransmit', None)
        config_kwargs['tunnel_fast_retransmit_min_age_ratio'] = getattr(
            args, 'fast_retransmit_min_age_ratio', None)
        config_kwargs['tunnel_fast_retransmit_max_per_seq'] = getattr(
            args, 'fast_retransmit_max_per_seq', None)
        config_kwargs['tunnel_adaptive_pacing_enabled'] = getattr(
            args, 'adaptive_pacing', None)
        config_kwargs['tunnel_pace_target_inflight_ratio'] = getattr(
            args, 'pace_target_inflight_ratio', None)
        config_kwargs['tunnel_pace_min_inflight'] = getattr(
            args, 'pace_min_inflight', None)
        config_kwargs['tunnel_pace_max_inflight'] = getattr(
            args, 'pace_max_inflight', None)
        config_kwargs['tunnel_pace_feedback_gain'] = getattr(
            args, 'pace_feedback_gain', None)
        config_kwargs['tunnel_pace_ack_ewma_alpha'] = getattr(
            args, 'pace_ack_ewma_alpha', None)
        config_kwargs['tunnel_pace_rtt_floor_ms'] = getattr(
            args, 'pace_rtt_floor_ms', None)
        config_kwargs['tunnel_pace_ack_idle_reset_sec'] = getattr(
            args, 'pace_ack_idle_reset_sec', None)
        config_kwargs['tunnel_poll_pacing_enabled'] = getattr(
            args, 'poll_pacing', None)
        config_kwargs['tunnel_poll_min_interval'] = getattr(
            args, 'poll_min_interval', None)
        config_kwargs['tunnel_poll_max_interval'] = getattr(
            args, 'poll_max_interval', None)
        config_kwargs['tunnel_poll_rtt_ratio'] = getattr(
            args, 'poll_rtt_ratio', None)

    # Server-specific
    if args.role == 'server':
        config_kwargs['file_transfer_root'] = getattr(args, 'root', None)
        config_kwargs['file_transfer_max_size'] = getattr(args, 'max_size', None)

    # Logging
    config_kwargs['db_log_path'] = getattr(args, 'db_log', None)
    config_kwargs['db_log_flush'] = getattr(args, 'db_log_flush', None)
    config_kwargs['db_log_queue'] = getattr(args, 'db_log_queue', None)
    config_kwargs['log_profile'] = getattr(args, 'log_profile', None)
    config_kwargs['relay_buffer_size'] = getattr(
        args, 'relay_buffer_size', None)
    config_kwargs['channel_max_send_buf'] = getattr(
        args, 'channel_max_send_buf', None)
    config_kwargs['relay_pump_backoff_max'] = getattr(
        args, 'relay_pump_backoff_max', None)
    config_kwargs['non_blocking_poll_timeout'] = getattr(
        args, 'non_blocking_poll_timeout', None)
    if getattr(args, 'xor', None) is not None:
        config_kwargs['crypto_mode'] = 'xor'
        config_kwargs['crypto_psk'] = _normalize_psk(args.xor)
    elif getattr(args, 'rc4', None) is not None:
        config_kwargs['crypto_mode'] = 'rc4'
        config_kwargs['crypto_psk'] = _normalize_psk(args.rc4)

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
        transport_cls = get_transport_class(args.transport, 'server')
        transport = transport_cls(config)
        tunnel = BobTunnel(transport, config, crypto=crypto)
    except TransportError as e:
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


def run_server_command(args, tunnel, logger, shutdown_requested):
    """Run server in command mode - wait for client, load module, execute."""
    try:
        module_loader = tunnel.enable_module_loader(logger=logger)

        # Start background serve loop
        tunnel.start_background()

        # Wait for client to connect
        if args.transport == 'dns':
            host, port = parse_host_port(
                tunnel._config.dns_listen_addr,
                default_port=53,
            )
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
                return 1
            time_provider.sleep(tunnel._config.tunnel_connect_poll_interval)

        log_event(
            logger,
            logging.INFO,
            'cli.client_connected',
            'Client connected',
            lambda: None,
        )

        module_name = args.module
        module_cls = AVAILABLE_MODULES[module_name]
        module_logger = get_logger('sfb.modules.%s' % module_name)
        remote_module = module_cls.REMOTE_MODULE or module_name
        log_event(
            logger,
            logging.INFO,
            'cli.module_load',
            'Loading module on peer',
            lambda: {'module': remote_module},
        )
        module_loader.load_remote(remote_module)
        log_event(
            logger,
            logging.INFO,
            'cli.module_loaded',
            'Module loaded (module=%s)' % remote_module,
            lambda: {'module': remote_module},
        )

        # Allow module message type
        tunnel.allow_message_type(module_cls.TYPE)

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
                        lambda: {'module': module_name},
                    )
                    return 1

        # Run module command
        return module_cls.run_command(args, tunnel, module_logger)

    except ModuleError as e:
        module_label = getattr(args, 'module', None) or 'module'
        reason = e.reason or str(e) or e.code
        _print_error('%s error: %s' % (module_label, reason))
        log_event(
            logger,
            logging.ERROR,
            'cli.module_error',
            'Module error',
            lambda: {'module': module_label, 'code': e.code, 'reason': reason},
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
        transport_cls = get_transport_class(args.transport, 'client')
        transport = transport_cls(config)
        tunnel = AliceTunnel(transport, config, crypto=crypto)
    except TransportError as e:
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


def main(args=None):
    """Main entry point."""
    parsed = parse_args(args)
    cert_result = _handle_tls_bump_generate_cert(parsed)
    if cert_result is not None:
        return cert_result
    if parsed.db_log is _DB_LOG_DEFAULT:
        # --db-log passed without a path, use default
        parsed.db_log = './logs/%s_log.db' % parsed.role
    if getattr(parsed, 'log_profile_explicit', False):
        parsed.verbose = True

    config = create_config(parsed)
    if parsed.log_profile:
        try:
            apply_log_profile(config, parsed.log_profile)
        except ValueError as e:
            _print_error(str(e))
            return 2
    if parsed.verbose and config.tunnel_pacer_summary_interval <= 0:
        config.tunnel_pacer_summary_interval = 1.0

    # Setup logging
    level = logging.DEBUG if parsed.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(name)s %(levelname)s %(message)s'
    )
    if parsed.db_log:
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
        formatter = logging.Formatter('%(name)s %(levelname)s %(message)s')
        add_sqlite_handler(
            logging.getLogger(),
            parsed.db_log,
            level=level,
            formatter=formatter,
            flush_interval=parsed.db_log_flush,
            queue_maxsize=parsed.db_log_queue,
        )
    add_component_filters(logging.getLogger(), config)
    logger = logging.getLogger('sfb')
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
            'db_log_path': parsed.db_log,
            'db_log_flush': parsed.db_log_flush,
            'db_log_queue': parsed.db_log_queue,
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

    # Create config and crypto
    crypto = create_crypto(parsed, logger)

    # Dispatch to role
    if parsed.role == 'server':
        return run_server(parsed, config, crypto, logger)
    else:
        return run_client(parsed, config, crypto, logger)


if __name__ == '__main__':
    sys.exit(main())
