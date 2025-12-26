# -*- coding: ascii -*-
"""Bob server - DNS tunnel endpoint with file transfer."""

from __future__ import absolute_import

import argparse
import logging
import os
import signal

from .config import Config
from .crypto import Plain, XOR
from .transport.dns import DnsServer
from .tunnel import BobTunnel
from .modules.file_transfer import FileTransferModule


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Bob DNS tunnel server with file transfer'
    )
    parser.add_argument(
        '--domain', required=True,
        help='Base domain for DNS tunnel (e.g., t.example.com)'
    )
    parser.add_argument(
        '--listen', default='0.0.0.0:5353',
        help='Listen address as host:port (default: 0.0.0.0:5353)'
    )
    parser.add_argument(
        '--psk',
        help='Pre-shared key for XOR encryption (omit for no encryption)'
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
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Enable debug logging'
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    logger = logging.getLogger('bob')

    # Change to root directory for file transfers
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        logger.error('Root directory does not exist: %s', root)
        return 1
    os.chdir(root)
    logger.info('File transfer root: %s', root)

    # Build config
    config = Config(
        dns_base_domain=args.domain,
        dns_listen_addr=args.listen,
        tunnel_idle_timeout=float(args.idle_timeout),
        file_transfer_max_size=args.max_size,
    )

    # Crypto
    if args.psk:
        crypto = XOR(args.psk.encode('utf-8'))
        logger.info('Encryption: XOR')
    else:
        crypto = Plain()
        logger.info('Encryption: none')

    # Components
    transport = DnsServer(config)
    tunnel = BobTunnel(transport, config, crypto=crypto)
    file_module = FileTransferModule(tunnel, logger=logger)

    # Signal handling for graceful shutdown
    def handle_signal(sig, frame):
        logger.info('Received signal %d, shutting down...', sig)
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Run
    logger.info('Listening on %s for domain %s', args.listen, args.domain)
    try:
        tunnel.serve_forever()
    except Exception as e:
        logger.exception('Error in serve loop: %s', e)
        return 1
    finally:
        file_module.shutdown()
        tunnel.close()
        logger.info('Shutdown complete')

    return 0


if __name__ == '__main__':
    exit(main())
