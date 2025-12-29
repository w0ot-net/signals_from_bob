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
import logging
import os
import signal
import sys
import time

from .config import Config
from .crypto import Plain, XOR
from .transport import TRANSPORTS, get_transport_class
from .tunnel import AliceTunnel, BobTunnel, TunnelState
from .modules import AVAILABLE_MODULES


# Role aliases
ROLE_ALIASES = {
    'bob': 'server',
    'alice': 'client',
    'server': 'server',
    'client': 'client',
}


def normalize_role(role):
    """Normalize role name (bob->server, alice->client)."""
    role = role.lower()
    if role not in ROLE_ALIASES:
        raise ValueError('Unknown role: %s (use: server, client, bob, alice)' % role)
    return ROLE_ALIASES[role]


def add_common_args(parser):
    """Add arguments shared by all roles."""
    parser.add_argument(
        '--role', required=True,
        help='Role: server (bob) or client (alice)'
    )
    parser.add_argument(
        '--transport', default='dns',
        choices=list(TRANSPORTS.keys()),
        help='Transport type (default: dns)'
    )
    parser.add_argument(
        '--domain', required=True,
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


def add_dns_server_args(parser):
    """Add DNS server-specific arguments."""
    parser.add_argument(
        '--dns_host', default='0.0.0.0',
        help='DNS server listen address (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--dns_port', type=int, default=53,
        help='DNS server listen port (default: 53)'
    )
    parser.add_argument(
        '--idle-timeout', type=int, default=300,
        help='Idle timeout in seconds (default: 300)'
    )


def add_dns_client_args(parser):
    """Add DNS client-specific arguments."""
    parser.add_argument(
        '--resolver',
        help='DNS resolver as host:port (default: auto-detect system resolver)'
    )
    parser.add_argument(
        '--qps', type=float, default=950.0,
        help='Max DNS queries per second (default: 950, 0=unlimited)'
    )


def add_module_args(parser):
    """Add module selection argument."""
    parser.add_argument(
        '--module',
        choices=list(AVAILABLE_MODULES.keys()),
        help='Module to load (required for command mode)'
    )


def add_server_args(parser):
    """Add server-specific arguments."""
    parser.add_argument(
        '--root', default='.',
        help='Root directory for file transfers (default: current dir)'
    )
    parser.add_argument(
        '--max-size', type=int, default=None,
        help='Max file size in bytes (default: unlimited)'
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
    add_common_args(parser)
    add_module_args(parser)

    partial_args, remaining = parser.parse_known_args(args)
    role = normalize_role(partial_args.role)
    transport = partial_args.transport

    # Second pass: full parser with role/transport/module-specific args
    parser = argparse.ArgumentParser(
        description='sfb - Signals From Bob tunnel'
    )
    add_common_args(parser)
    add_module_args(parser)

    # Transport-specific args
    if transport == 'dns':
        if role == 'server':
            add_dns_server_args(parser)
        else:
            add_dns_client_args(parser)

    # Server-specific args
    if role == 'server':
        add_server_args(parser)

    # Module subcommands
    if partial_args.module:
        module_cls = AVAILABLE_MODULES[partial_args.module]
        subparsers = parser.add_subparsers(dest='command', help='Module commands')
        module_cls.register_commands(subparsers, role)

    parsed = parser.parse_args(args)
    parsed.role = normalize_role(parsed.role)  # Normalize in final result
    return parsed


def create_config(args):
    """Create Config from parsed arguments."""
    config_kwargs = {
        'dns_base_domain': args.domain,
    }

    # DNS transport args
    if args.transport == 'dns':
        if args.role == 'server':
            host = getattr(args, 'dns_host', '0.0.0.0')
            port = getattr(args, 'dns_port', 53)
            config_kwargs['dns_listen_addr'] = '%s:%d' % (host, port)
            config_kwargs['tunnel_idle_timeout'] = float(args.idle_timeout)
        else:
            config_kwargs['dns_resolver'] = getattr(args, 'resolver', None)
            config_kwargs['dns_queries_per_second'] = getattr(args, 'qps', 950.0)

    # Server-specific
    if args.role == 'server':
        config_kwargs['file_transfer_max_size'] = getattr(args, 'max_size', None)

    return Config(**config_kwargs)


def create_crypto(args, logger):
    """Create crypto instance from args."""
    if args.psk:
        crypto = XOR(args.psk.encode('utf-8'))
        logger.info('Encryption: XOR')
    else:
        crypto = Plain()
        logger.info('Encryption: none')
    return crypto


def run_server(args, config, crypto, logger):
    """Run in server role."""
    # Change to root directory for file transfers
    root = os.path.abspath(getattr(args, 'root', '.'))
    if not os.path.isdir(root):
        logger.error('Root directory does not exist: %s', root)
        return 1
    os.chdir(root)
    logger.info('Working directory: %s', root)

    # Create transport and tunnel
    transport_cls = get_transport_class(args.transport, 'server')
    transport = transport_cls(config)
    tunnel = BobTunnel(transport, config, crypto=crypto)

    # Signal handling
    shutdown_requested = [False]

    def handle_signal(sig, frame):
        if shutdown_requested[0]:
            sys.exit(1)
        shutdown_requested[0] = True
        logger.info('Shutting down...')
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Check if we have a module command to execute
    if args.module and getattr(args, 'command', None):
        return run_server_command(args, tunnel, logger, shutdown_requested)
    else:
        return run_server_passive(args, tunnel, logger)


def run_server_passive(args, tunnel, logger):
    """Run server in passive mode (no command, just wait for connections)."""
    host = getattr(args, 'dns_host', '0.0.0.0')
    port = getattr(args, 'dns_port', 53)
    logger.info('Listening on %s:%d for domain %s (passive mode)', host, port, args.domain)
    try:
        tunnel.serve_forever()
    except Exception as e:
        logger.exception('Error in serve loop: %s', e)
        return 1
    finally:
        tunnel.close()
        logger.info('Shutdown complete')
    return 0


def run_server_command(args, tunnel, logger, shutdown_requested):
    """Run server in command mode - wait for client, load module, execute."""
    try:
        module_loader = tunnel.enable_module_loader(logger=logger)

        # Start background serve loop
        tunnel.start_background()

        # Wait for client to connect
        host = getattr(args, 'dns_host', '0.0.0.0')
        port = getattr(args, 'dns_port', 53)
        logger.info('Waiting for client on %s:%d...', host, port)
        while tunnel._state != TunnelState.CONNECTED:
            if shutdown_requested[0]:
                return 1
            time.sleep(0.1)

        logger.info('Client connected')

        # Load module on peer
        module_name = args.module
        logger.info('Loading module %s on peer...', module_name)
        module_loader.load_remote(module_name)
        logger.info('Module %s loaded', module_name)

        # Allow module message type
        module_cls = AVAILABLE_MODULES[module_name]
        tunnel.allow_message_type(module_cls.TYPE)

        # Run module command
        return module_cls.run_command(args, tunnel, logger)

    except Exception as e:
        logger.error('Error: %s', e)
        if args.verbose:
            logger.exception('Full traceback:')
        return 1

    finally:
        tunnel.close()
        logger.info('Shutdown complete')


def run_client(args, config, crypto, logger):
    """Run in client role."""
    # Create transport and tunnel
    transport_cls = get_transport_class(args.transport, 'client')
    transport = transport_cls(config)
    tunnel = AliceTunnel(transport, config, crypto=crypto)

    # Signal handling
    shutdown_requested = [False]

    def handle_signal(sig, frame):
        if shutdown_requested[0]:
            sys.exit(1)
        shutdown_requested[0] = True
        logger.info('Shutting down...')
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Connect
        resolver_desc = getattr(args, 'resolver', None) or 'system resolver'
        logger.info('Connecting to %s via %s...', args.domain, resolver_desc)
        tunnel.connect()
        logger.info('Connected')

        # Start background tick loop
        tunnel.start_background()

        logger.info('Waiting for commands from server...')

        # Run until connection closes or signal received
        while tunnel._state == TunnelState.CONNECTED and not shutdown_requested[0]:
            time.sleep(0.1)

        return 0

    except Exception as e:
        logger.error('Error: %s', e)
        if args.verbose:
            logger.exception('Full traceback:')
        return 1

    finally:
        tunnel.close()
        logger.info('Shutdown complete')


def main(args=None):
    """Main entry point."""
    parsed = parse_args(args)

    # Setup logging
    level = logging.DEBUG if parsed.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    logger = logging.getLogger('sfb')

    # Create config and crypto
    config = create_config(parsed)
    crypto = create_crypto(parsed, logger)

    # Dispatch to role
    if parsed.role == 'server':
        return run_server(parsed, config, crypto, logger)
    else:
        return run_client(parsed, config, crypto, logger)


if __name__ == '__main__':
    sys.exit(main())
