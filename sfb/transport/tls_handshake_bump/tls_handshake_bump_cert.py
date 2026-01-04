# -*- coding: ascii -*-
"""
TLS handshake bump certificate builder.
"""

from __future__ import absolute_import

import base64

from . import tls_handshake_bump_cert_template as cert_template
from ...compat import PY2, text_type


def _load_cert_template():
    template = base64.b64decode(cert_template.CERT_TEMPLATE_DER_B64)
    placeholder = b'a' * cert_template.CN_LEN
    for offset in cert_template.CN_OFFSETS:
        if template[offset:offset + cert_template.CN_LEN] != placeholder:
            raise ValueError('TLS bump cert template CN placeholder mismatch')
    return template


_CERT_TEMPLATE_DER = _load_cert_template()


def build_cert_der(cn_text):
    if not isinstance(cn_text, text_type):
        raise TypeError('CN must be text')
    try:
        cn_text.encode('ascii')
    except UnicodeError:
        raise ValueError('CN must be ASCII')
    if not cn_text:
        raise ValueError('CN must not be empty')
    if len(cn_text) > cert_template.CN_LEN:
        raise ValueError('CN exceeds template length')
    padded = cn_text + ('a' * (cert_template.CN_LEN - len(cn_text)))
    cn_bytes = padded.encode('ascii')
    template = bytearray(_CERT_TEMPLATE_DER)
    for offset in cert_template.CN_OFFSETS:
        template[offset:offset + cert_template.CN_LEN] = cn_bytes
    if PY2:
        return template.tostring()
    return bytes(template)
