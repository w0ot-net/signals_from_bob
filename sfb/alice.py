# -*- coding: ascii -*-
"""Alice client - DNS tunnel endpoint with file transfer."""

from __future__ import absolute_import

import logging
import signal
import sys
import time

from .argparse_utils import parse_args
from .config import Config
from .crypto import Plain, XOR
from .transport.dns import DnsClient
from .tunnel import AliceTunnel, TunnelState
from .modules import ModuleLoader


def main():
    """Main entry point."""
    args = parse_args(role='client')

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    logger = logging.getLogger('alice')

    # Build config
    config = Config(
        dns_base_domain=args.domain,
        dns_resolver=args.resolver,
        dns_queries_per_second=args.qps,
    )

    # Crypto
    if args.psk:
        crypto = XOR(args.psk.encode('utf-8'))
        logger.info('Encryption: XOR')
    else:
        crypto = Plain()
        logger.info('Encryption: none')

    # Components
    transport = DnsClient(config)
    tunnel = AliceTunnel(transport, config, crypto=crypto)
    module_loader = None

    # Signal handling
    shutdown_requested = [False]  # Use list for mutable closure

    def handle_signal(sig, frame):
        if shutdown_requested[0]:
            # Second signal - force exit
            sys.exit(1)
        shutdown_requested[0] = True
        logger.info('Received signal %d, shutting down...', sig)
        tunnel.close()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Connect
        resolver_desc = args.resolver or 'system resolver'
        logger.info('Connecting to %s via %s...', args.domain, resolver_desc)
        tunnel.connect(timeout=args.timeout)
        logger.info('Connected')

        # Start background tick loop
        tunnel.start_background()

        # Create module loader to handle Bob's module load requests
        module_loader = ModuleLoader(tunnel, logger=logger)
        logger.info('Waiting for commands from Bob...')

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
        if module_loader:
            module_loader.shutdown()
        tunnel.close()  # This also stops the background thread
        logger.info('Shutdown complete')


if __name__ == '__main__':
    sys.exit(main())
