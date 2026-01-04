# -*- coding: ascii -*-
from __future__ import absolute_import

import unittest

from sfb.transport.tls_handshake_bump import (
    tls_handshake_bump_cert_template as cert_template,
)


class TlsHandshakeBumpCertTemplateTests(unittest.TestCase):
    def test_build_cert_der_pads_cn(self):
        cn = 'abc'
        cert = cert_template.build_cert_der(cn)
        padded = cn + ('a' * (cert_template.CN_LEN - len(cn)))
        cn_bytes = padded.encode('ascii')
        for offset in cert_template.CN_OFFSETS:
            self.assertEqual(
                cert[offset:offset + cert_template.CN_LEN],
                cn_bytes,
            )

    def test_build_cert_der_rejects_long_cn(self):
        cn = 'a' * (cert_template.CN_LEN + 1)
        with self.assertRaises(ValueError):
            cert_template.build_cert_der(cn)


if __name__ == '__main__':
    unittest.main()
