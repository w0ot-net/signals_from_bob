# -*- coding: ascii -*-
"""Bob server - DNS tunnel endpoint with file transfer."""

from __future__ import absolute_import

import logging
import os
import signal
import sys
import threading
import time

from .argparse_utils import parse_args
from .config import Config
from .crypto import Plain, XOR
from .transport.dns import DnsServer
from .tunnel import BobTunnel
from .modules.file_transfer import FileTransferModule


class TunnelRunner(object):
    """Runs the tunnel tick loop in a background thread."""

    def __init__(self, tunnel, logger):
        self._tunnel = tunnel
        self._logger = logger
        self._stop = False
        self._thread = None

    def start(self):
        """Start the background tick loop."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background tick loop."""
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        """Background tick loop."""
        while not self._stop:
            try:
                if not self._tunnel.tick():
                    break
            except Exception as e:
                self._logger.warning('Tick error: %s', e)
            time.sleep(0.001)


def main():
    """Main entry point."""
    args = parse_args(role='server')

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

    # Run
    logger.info('Listening on %s for domain %s', args.listen, args.domain)
    runner = None
    try:
        if args.command:
            # Wait for Alice to connect, then execute command
            if not tunnel.wait_for_connection():
                logger.error('Connection failed or timed out')
                return 1

            # Start background tick loop to handle Alice's polls
            runner = TunnelRunner(tunnel, logger)
            runner.start()

            logger.info('Executing command: %s', args.command)

            if args.command == 'list':
                result = file_module.list_dir(args.path, timeout=30.0)
                for entry in result:
                    if entry.get('dir'):
                        print('d %10s %s/' % ('-', entry['name']))
                    else:
                        print('- %10d %s' % (entry.get('size', 0), entry['name']))

            elif args.command == 'get':
                local_path = args.local or os.path.basename(args.remote)
                logger.info('Downloading %s -> %s', args.remote, local_path)
                file_module.get(args.remote, local_path, timeout=30.0)
                logger.info('Download complete: %s', local_path)

            elif args.command == 'put':
                if not os.path.isfile(args.local):
                    logger.error('Local file not found: %s', args.local)
                    return 1
                size = os.path.getsize(args.local)
                logger.info('Uploading %s (%d bytes) -> %s', args.local, size, args.remote)
                file_module.put(args.local, args.remote, timeout=30.0)
                logger.info('Upload complete: %s', args.remote)
        else:
            # No command - serve forever (respond to Alice's requests)
            tunnel.serve_forever()
    except Exception as e:
        logger.exception('Error: %s', e)
        return 1
    finally:
        if runner:
            runner.stop()
        file_module.shutdown()
        tunnel.close()
        logger.info('Shutdown complete')

    return 0


if __name__ == '__main__':
    exit(main())
