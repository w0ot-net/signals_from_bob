# -*- coding: ascii -*-
"""
Argument parsing utilities for the DNS tunnel.

Provides centralized argument parsing for client and server roles,
with module-specific subcommands registered by each module.
"""

from __future__ import absolute_import

import argparse


def add_common_args(parser):
    """Add arguments shared by client and server."""
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


def add_client_args(parser):
    """Add client-specific arguments."""
    parser.add_argument(
        '--resolver',
        help='DNS resolver as host:port (default: auto-detect system resolver)'
    )
    parser.add_argument(
        '--timeout', type=float, default=30.0,
        help='Operation timeout in seconds (default: 30)'
    )
    parser.add_argument(
        '--qps', type=float, default=950.0,
        help='Max DNS queries per second (default: 950, 0=unlimited)'
    )


def add_server_args(parser):
    """Add server-specific arguments."""
    parser.add_argument(
        '--listen', default='0.0.0.0:53',
        help='Listen address as host:port (default: 0.0.0.0:53)'
    )
    parser.add_argument(
        '--root', default='.',
        help='Root directory for file transfers (default: current dir)'
    )
    parser.add_argument(
        '--idle-timeout', type=int, default=300,
        help='Idle timeout in seconds (default: 300)'
    )
    parser.add_argument(
        '--max-size', type=int, default=None,
        help='Max file size in bytes (default: unlimited)'
    )


def parse_args(role, args=None):
    """
    Parse command-line arguments for the given role.

    Args:
        role: 'client' or 'server'
        args: Optional list of arguments (defaults to sys.argv)

    Returns:
        Parsed argparse.Namespace object.
    """
    from .modules import AVAILABLE_MODULES

    if role == 'client':
        desc = 'Alice DNS tunnel client with file transfer'
    else:
        desc = 'Bob DNS tunnel server with file transfer'

    parser = argparse.ArgumentParser(description=desc)

    add_common_args(parser)

    if role == 'client':
        add_client_args(parser)
    else:
        add_server_args(parser)

    # Module selection
    parser.add_argument(
        '--module', choices=list(AVAILABLE_MODULES.keys()),
        default='file_transfer',
        help='Module to use (default: file_transfer)'
    )

    # Two-pass parsing: first get --module, then register its subcommands
    partial_args, _ = parser.parse_known_args(args)

    # Only register subcommands from the selected module
    mod_class = AVAILABLE_MODULES[partial_args.module]
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    mod_class.register_commands(subparsers, role)

    return parser.parse_args(args)
