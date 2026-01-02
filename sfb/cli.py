# -*- coding: ascii -*-
"""
Generic CLI for sfb tunnel.

Provides a unified entry point supporting:
- Roles: server (bob) or client (alice)
- Transports: dns (extensible)
- Modules: file_transfer, socks_server, socks_relay, etc.
"""

from __future__ import absolute_import

import argparse
import errno
import logging
import os
import signal
import sys

from .config import Config
from .crypto import Plain, XOR
from .logging_util import add_component_filters, add_sqlite_handler, get_logger, log_event
from .log_profiles import LOG_PROFILES, apply_log_profile
from .transport import TRANSPORTS, TransportError, get_transport_class
from .tunnel import AliceTunnel, BobTunnel, TunnelState
from .modules import AVAILABLE_MODULES
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


def _split_host_port(addr, default_port):
    if ':' in addr:
        host, port = addr.rsplit(':', 1)
        return host, int(port)
    return addr, default_port


def normalize_role(role):
    """Normalize role name (bob->server, alice->client)."""
    role = role.lower()
    if role not in ROLE_ALIASES:
        raise ValueError('Unknown role: %s (use: server, client, bob, alice)' % role)
    return ROLE_ALIASES[role]


def add_common_args(parser, config, require_domain=True):
    """Add arguments shared by all roles."""
    parser.add_argument(
        '--role', required=True,
        help='Role: server (bob) or client (alice)'
    )
    parser.add_argument(
        '--transport', default=config.transport_default,
        choices=list(TRANSPORTS.keys()),
        help='Transport type (default: %s)' % config.transport_default
    )
    parser.add_argument(
        '--max_in_flight', type=int, default=config.max_in_flight,
        help='Max in-flight packets (1-256, default: %s)' %
             config.max_in_flight
    )
    parser.add_argument(
        '--domain',
        required=require_domain,
        default=config.dns_base_domain,
        help='Base domain for DNS tunnel (e.g., t.example.com)'
    )
    parser.add_argument(
        '--psk',
        help='Pre-shared key for XOR encryption (omit for no encryption)'
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
        help='Enable SQLite logging to PATH'
    )
    parser.add_argument(
        '--db-log-flush', type=float, default=config.db_log_flush,
        help='SQLite log flush interval in seconds (default: 0.5)'
    )
    parser.add_argument(
        '--db-log-queue', type=int, default=config.db_log_queue,
        help='SQLite log queue max size (default: 0=unbounded)'
    )
    parser.add_argument(
        '--socks_relay_buffer_size', type=int,
        default=config.socks_relay_buffer_size,
        help='SOCKS relay buffer size in bytes (default: %s)' %
             config.socks_relay_buffer_size
    )
    parser.add_argument(
        '--channel_max_send_buf', type=int,
        default=config.channel_max_send_buf,
        help='Channel max send buffer in bytes (default: %s)' %
             config.channel_max_send_buf
    )
    parser.add_argument(
        '--socks_pump_backoff_max', type=float,
        default=config.socks_pump_backoff_max,
        help='SOCKS pump max poll backoff in seconds (default: %s)' %
             config.socks_pump_backoff_max
    )
    parser.add_argument(
        '--non_blocking_poll_timeout', type=float,
        default=config.non_blocking_poll_timeout,
        help='Non-blocking poll timeout in seconds (default: %s)' %
             config.non_blocking_poll_timeout
    )
    parser.add_argument(
        '--log-profile',
        default=config.log_profile,
        choices=sorted(LOG_PROFILES.keys()),
        help='Logging profile name (default: %s)' % config.log_profile
    )


def add_dns_server_args(parser, config):
    """Add DNS server-specific arguments."""
    host, port = _split_host_port(config.dns_listen_addr, 53)
    parser.add_argument(
        '--dns_host', default=host,
        help='DNS server listen address (default: %s)' % host
    )
    parser.add_argument(
        '--dns_port', type=int, default=port,
        help='DNS server listen port (default: %s)' % port
    )
    parser.add_argument(
        '--idle-timeout', type=int, default=config.tunnel_idle_timeout,
        help='Idle timeout in seconds (default: %s)' % config.tunnel_idle_timeout
    )


def add_dns_client_args(parser, config):
    """Add DNS client-specific arguments."""
    parser.add_argument(
        '--resolver',
        default=config.dns_resolver,
        help='DNS resolver as host:port (default: auto-detect system resolver)'
    )


def add_icmp_common_args(parser, config):
    """Add ICMP arguments shared by client and server."""
    parser.add_argument(
        '--icmp_mtu', type=int, default=config.icmp_payload_mtu,
        help='Max ICMP payload size in bytes (default: %s)' %
             config.icmp_payload_mtu
    )


def add_icmp_client_args(parser, config, require_target=True):
    """Add ICMP client-specific arguments."""
    parser.add_argument(
        '--icmp_target',
        default=config.icmp_target,
        required=require_target,
        help='ICMP target host or IP for client'
    )


def add_client_pacing_args(parser, config):
    """Add transport-agnostic client pacing arguments."""
    parser.add_argument(
        '--send_rate', type=float, default=config.tunnel_send_rate,
        help='Max packets per second from Alice (0=unlimited, default: %s)' %
             config.tunnel_send_rate
    )
    parser.add_argument(
        '--send_burst', type=float, default=config.tunnel_send_burst,
        help='Burst capacity for send rate (packets, default: %s)' %
             (config.tunnel_send_burst if config.tunnel_send_burst is not None else
              'same as send_rate')
    )
    parser.add_argument(
        '--adaptive_pacing', dest='adaptive_pacing', action='store_true',
        default=config.tunnel_adaptive_pacing_enabled,
        help='Enable adaptive pacing (default: %s)' %
             config.tunnel_adaptive_pacing_enabled
    )
    parser.add_argument(
        '--no_adaptive_pacing', dest='adaptive_pacing', action='store_false',
        help='Disable adaptive pacing'
    )
    parser.add_argument(
        '--pace_target_inflight_ratio', type=float,
        default=config.tunnel_pace_target_inflight_ratio,
        help='Adaptive pacing target inflight ratio (default: %s)' %
             config.tunnel_pace_target_inflight_ratio
    )
    parser.add_argument(
        '--pace_min_inflight', type=int,
        default=config.tunnel_pace_min_inflight,
        help='Adaptive pacing minimum inflight (default: %s)' %
             config.tunnel_pace_min_inflight
    )
    parser.add_argument(
        '--pace_max_inflight', type=int,
        default=config.tunnel_pace_max_inflight,
        help='Adaptive pacing maximum inflight (default: %s)' %
             config.tunnel_pace_max_inflight
    )
    parser.add_argument(
        '--pace_feedback_gain', type=float,
        default=config.tunnel_pace_feedback_gain,
        help='Adaptive pacing feedback gain (default: %s)' %
             config.tunnel_pace_feedback_gain
    )
    parser.add_argument(
        '--pace_ack_ewma_alpha', type=float,
        default=config.tunnel_pace_ack_ewma_alpha,
        help='Adaptive pacing ACK EWMA alpha (default: %s)' %
             config.tunnel_pace_ack_ewma_alpha
    )
    parser.add_argument(
        '--pace_rtt_floor_ms', type=float,
        default=config.tunnel_pace_rtt_floor_ms,
        help='Adaptive pacing RTT floor ms (default: %s)' %
             config.tunnel_pace_rtt_floor_ms
    )
    parser.add_argument(
        '--pace_ack_idle_reset_sec', type=float,
        default=config.tunnel_pace_ack_idle_reset_sec,
        help='Adaptive pacing ACK idle reset sec (default: %s)' %
             config.tunnel_pace_ack_idle_reset_sec
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
        help='Root directory for file transfers (default: %s)' % config.file_transfer_root
    )
    parser.add_argument(
        '--max-size', type=int, default=config.file_transfer_max_size,
        help='Max file size in bytes (default: %s)' % config.file_transfer_max_size
    )


def parse_args(args=None):
    """
    Parse command-line arguments.

    Uses two-pass parsing:
    1. First pass gets --role, --transport, --module
    2. Second pass adds role/transport/module-specific args
    """
    # First pass: get basic options
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel',
        add_help=False,  # Add help in second pass
    )
    config_defaults = Config()
    add_common_args(parser, config_defaults, require_domain=False)
    add_module_args(parser)

    partial_args, remaining = parser.parse_known_args(args)
    role = normalize_role(partial_args.role)
    transport = partial_args.transport

    # Second pass: full parser with role/transport/module-specific args
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel'
    )
    add_common_args(parser, config_defaults, require_domain=(transport == 'dns'))
    add_module_args(parser)

    # Transport-specific args
    if transport == 'dns':
        if role == 'server':
            add_dns_server_args(parser, config_defaults)
        else:
            add_dns_client_args(parser, config_defaults)
    elif transport == 'icmp':
        add_icmp_common_args(parser, config_defaults)
        if role == 'client':
            add_icmp_client_args(parser, config_defaults, require_target=True)
    if role == 'client':
        add_client_pacing_args(parser, config_defaults)

    # Server-specific args
    if role == 'server':
        add_server_args(parser, config_defaults)

    # Module subcommands
    if partial_args.module:
        module_cls = AVAILABLE_MODULES[partial_args.module]
        subparsers = parser.add_subparsers(dest='command', help='Module commands')
        module_cls.register_commands(subparsers, role, config=config_defaults)

    parsed = parser.parse_args(args)
    parsed.role = normalize_role(parsed.role)  # Normalize in final result
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
            host = getattr(args, 'dns_host', None)
            port = getattr(args, 'dns_port', None)
            if host is None or port is None:
                host, port = _split_host_port(Config().dns_listen_addr, 53)
            config_kwargs['dns_listen_addr'] = '%s:%d' % (host, port)
            config_kwargs['tunnel_idle_timeout'] = float(args.idle_timeout)
        else:
            config_kwargs['dns_resolver'] = getattr(args, 'resolver', None)
    elif args.transport == 'icmp':
        config_kwargs['icmp_payload_mtu'] = getattr(args, 'icmp_mtu', None)
        if args.role == 'client':
            config_kwargs['icmp_target'] = getattr(args, 'icmp_target', None)

    if args.role == 'client':
        config_kwargs['tunnel_send_rate'] = getattr(args, 'send_rate', None)
        config_kwargs['tunnel_send_burst'] = getattr(args, 'send_burst', None)
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

    # Server-specific
    if args.role == 'server':
        config_kwargs['file_transfer_root'] = getattr(args, 'root', None)
        config_kwargs['file_transfer_max_size'] = getattr(args, 'max_size', None)

    # Logging
    config_kwargs['db_log_path'] = getattr(args, 'db_log', None)
    config_kwargs['db_log_flush'] = getattr(args, 'db_log_flush', None)
    config_kwargs['db_log_queue'] = getattr(args, 'db_log_queue', None)
    config_kwargs['log_profile'] = getattr(args, 'log_profile', None)
    config_kwargs['socks_relay_buffer_size'] = getattr(
        args, 'socks_relay_buffer_size', None)
    config_kwargs['channel_max_send_buf'] = getattr(
        args, 'channel_max_send_buf', None)
    config_kwargs['socks_pump_backoff_max'] = getattr(
        args, 'socks_pump_backoff_max', None)
    config_kwargs['non_blocking_poll_timeout'] = getattr(
        args, 'non_blocking_poll_timeout', None)

    config_kwargs = {k: v for k, v in config_kwargs.items() if v is not None}
    return Config(**config_kwargs)


def create_crypto(args, logger):
    """Create crypto instance from args."""
    if args.psk:
        crypto = XOR(args.psk.encode('utf-8'))
        log_event(
            logger,
            logging.INFO,
            'cli.crypto',
            'Encryption enabled',
            lambda: {'mode': 'xor'},
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
            sys.exit(1)
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
        host, port = _split_host_port(tunnel._config.dns_listen_addr, 53)
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
            host, port = _split_host_port(tunnel._config.dns_listen_addr, 53)
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
            'Module loaded',
            lambda: {'module': remote_module},
        )

        # Allow module message type
        tunnel.allow_message_type(module_cls.TYPE)

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
            sys.exit(1)
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
            resolver_desc = getattr(args, 'resolver', None) or 'system resolver'
            log_event(
                logger,
                logging.INFO,
                'cli.connect',
                'Connecting',
                lambda: {'transport': 'dns', 'domain': args.domain, 'resolver': resolver_desc},
            )
        elif args.transport == 'icmp':
            target = getattr(args, 'icmp_target', None)
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
    if parsed.db_log is _DB_LOG_DEFAULT:
        # --db-log passed without a path, use default
        parsed.db_log = './logs/%s_log.db' % parsed.role

    config = create_config(parsed)
    if parsed.log_profile:
        try:
            apply_log_profile(config, parsed.log_profile)
        except ValueError as e:
            _print_error(str(e))
            return 2

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

    # Create config and crypto
    crypto = create_crypto(parsed, logger)

    # Dispatch to role
    if parsed.role == 'server':
        return run_server(parsed, config, crypto, logger)
    else:
        return run_client(parsed, config, crypto, logger)


if __name__ == '__main__':
    sys.exit(main())
