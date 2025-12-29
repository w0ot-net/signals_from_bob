# -*- coding: ascii -*-
"""Bob server - DNS tunnel endpoint with file transfer."""

from __future__ import absolute_import

import logging
import os
import signal
import sys
import time

from .argparse_utils import parse_args
from .config import Config
from .crypto import Plain, XOR
from .transport.dns import DnsServer
from .tunnel import BobTunnel, TunnelState
from .modules import FileTransferModule


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

    # Check if we have a command to execute
    if hasattr(args, 'command') and args.command:
        return run_command_mode(args, tunnel, logger, shutdown_requested)
    else:
        return run_serve_mode(args, tunnel, logger)


def run_serve_mode(args, tunnel, logger):
    """Run in passive serve mode (no command, just wait for connections)."""
    # In serve mode, we don't create modules - Alice will handle requests
    logger.info('Listening on %s for domain %s (serve mode)', args.listen, args.domain)
    try:
        tunnel.serve_forever()
    except Exception as e:
        logger.exception('Error in serve loop: %s', e)
        return 1
    finally:
        tunnel.close()
        logger.info('Shutdown complete')
    return 0


def run_command_mode(args, tunnel, logger, shutdown_requested):
    """Run in command mode - wait for Alice, load module, execute command."""
    file_module = None

    try:
        module_loader = tunnel.enable_module_loader(logger=logger)

        # Start background serve loop
        tunnel.start_background()

        # Wait for Alice to connect
        logger.info('Waiting for Alice to connect on %s...', args.listen)
        timeout = getattr(args, 'timeout', None)
        start_time = time.time()
        while tunnel._state != TunnelState.CONNECTED:
            if shutdown_requested[0]:
                return 1
            if timeout is not None and time.time() - start_time > timeout:
                logger.error('Timeout waiting for connection')
                return 1
            time.sleep(0.1)

        logger.info('Alice connected')

        # Create module loader and request module load on Alice
        module_name = args.module
        logger.info('Loading module %s on Alice...', module_name)
        module_loader.load_remote(module_name, timeout=timeout)
        logger.info('Module %s loaded on Alice', module_name)

        # Now allow 'file' messages for file transfer responses
        tunnel.allow_message_type('file')

        # Create local file module to send requests
        file_module = FileTransferModule(tunnel, logger=logger)

        # Execute command
        if args.command == 'list':
            result = file_module.list_dir(args.path, timeout=timeout)
            for entry in result:
                if entry.get('dir'):
                    print('d %10s %s/' % ('-', entry['name']))
                else:
                    print('- %10d %s' % (entry.get('size', 0), entry['name']))

        elif args.command == 'get':
            local_path = args.local or os.path.basename(args.remote)
            logger.info('Downloading %s -> %s', args.remote, local_path)
            file_module.get(args.remote, local_path, timeout=timeout)
            logger.info('Download complete: %s', local_path)

        elif args.command == 'put':
            if not os.path.isfile(args.local):
                logger.error('Local file not found: %s', args.local)
                return 1
            size = os.path.getsize(args.local)
            logger.info('Uploading %s (%d bytes) -> %s', args.local, size, args.remote)
            file_module.put(args.local, args.remote, timeout=timeout)
            logger.info('Upload complete: %s', args.remote)

        return 0

    except Exception as e:
        logger.error('Error: %s', e)
        if args.verbose:
            logger.exception('Full traceback:')
        return 1

    finally:
        if file_module:
            file_module.shutdown()
        tunnel.close()  # This also stops the background thread
        logger.info('Shutdown complete')


if __name__ == '__main__':
    exit(main())
