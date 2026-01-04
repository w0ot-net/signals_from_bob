#!/usr/bin/env python3
# -*- coding: ascii -*-
"""
Generate a DER certificate for the TLS handshake bump transport.

Usage: python3 scripts/tls_bump_cert_helper.py <cn> <out_der_path>
Requires openssl in PATH.
"""

from __future__ import absolute_import

import os
import shutil
import subprocess
import sys
import tempfile


def main(argv):
    if len(argv) != 3:
        sys.stderr.write(
            'Usage: python3 scripts/tls_bump_cert_helper.py <cn> <out_der_path>\n'
        )
        return 2
    cn = argv[1]
    out_path = argv[2]
    try:
        cn.encode('ascii')
    except UnicodeError:
        sys.stderr.write('CN must be ASCII\n')
        return 2

    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    temp_dir = tempfile.mkdtemp(prefix='tls_bump_cert_')
    try:
        key_path = os.path.join(temp_dir, 'key.pem')
        cert_pem = os.path.join(temp_dir, 'cert.pem')
        subprocess.check_call([
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-nodes',
            '-days',
            '1',
            '-subj',
            '/CN=%s' % cn,
            '-keyout',
            key_path,
            '-out',
            cert_pem,
        ])
        subprocess.check_call([
            'openssl',
            'x509',
            '-in',
            cert_pem,
            '-outform',
            'der',
            '-out',
            out_path,
        ])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
