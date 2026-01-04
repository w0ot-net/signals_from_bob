# -*- coding: ascii -*-
"""
TLS handshake bump in-memory certificate template.
"""

from __future__ import absolute_import

import base64

from ...compat import PY2, text_type


# 96 chars keeps CN payload capacity above the packet header size and matches
# the previous TLS bump CN cap for broad compatibility.
CN_LEN = 96
CN_OFFSETS = (63, 204)

_CERT_TEMPLATE_DER_B64 = (
    b'MIIDtzCCAp+gAwIBAgIUOWH8Ju2RPDYUpw+pJ9lrbkqR94YwDQYJKoZIhvcNAQELBQAwazFpMGcG'
    b'A1UEAwxgYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhMB4XDTI2MDEwNDIx'
    b'NDYxMFoXDTI2MDEwNTIxNDYxMFowazFpMGcGA1UEAwxgYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4dnc0lZi9Ff6'
    b'Wuy6X2yn8E1oMfdj2MnraRnRmbaTqynt8Y12TTO4zGWujtTBJR4meGVz8yVY1bC5Rr3o8teQ8GAO'
    b'wXhQgJWN33KFg0h8IK1YR+cG1zxFTSlGpTxpP+ILXysV2fWFsoaQ7QVvlesw8htBzAjqQWRA/dQy'
    b'QopN3/rZzwVlEfCQRbOAswsxNSmULfsPZzatN+k/Wj0PZ8pxcIlxptn8Yxevu4/nPn4AcI/+cxD+'
    b'Nsnkm8llG/hec7b5e+5Zo/42MgscZIrO7ErpazuQmb5UwoIa6523SvTEaI6/3sW5pgFKlYBNaU75'
    b'89JAV089QAIGgarTuJLUbgTF5QIDAQABo1MwUTAdBgNVHQ4EFgQUlWCcEVqZ3h0gjcOXIFk7cKXD'
    b'c9wwHwYDVR0jBBgwFoAUlWCcEVqZ3h0gjcOXIFk7cKXDc9wwDwYDVR0TAQH/BAUwAwEB/zANBgkq'
    b'hkiG9w0BAQsFAAOCAQEAgJyZGwPZoRCzM4Q/pMEjz7PS6cJIIkQKzvPp1Muf0SWOhGzIW5O56WWi'
    b'qCGPysQUOSUOTROzSwsamABxeH+/SFo0oOB8SvFmnHpu2NxI/gloMLgc02P7xPxVt3OEhH+SM7+n'
    b'+epYr9hMSjuehmKyz6D989mdpw4FcqhWnhzcpRY+aNylG3ajSLcU04MS2jiKaC/cWekTurgGR6eO'
    b'6aap4ygtQYUBYKSpAVB+FkkTRNzGF3g6SJTAVB1g8UIVyD/XHESdxluWgLe4lOaa/bw0eXyola96'
    b'Mdp2ArPzErHzVg7bfszr+O1eqg5FDAMJUtwP62F0vGH0FXjNpKJiVVRriA=='
)
_CERT_TEMPLATE_DER = base64.b64decode(_CERT_TEMPLATE_DER_B64)

_PLACEHOLDER = b'a' * CN_LEN
for _offset in CN_OFFSETS:
    if _CERT_TEMPLATE_DER[_offset:_offset + CN_LEN] != _PLACEHOLDER:
        raise ValueError('TLS bump cert template CN placeholder mismatch')


def build_cert_der(cn_text):
    if not isinstance(cn_text, text_type):
        raise TypeError('CN must be text')
    try:
        cn_text.encode('ascii')
    except UnicodeError:
        raise ValueError('CN must be ASCII')
    if not cn_text:
        raise ValueError('CN must not be empty')
    if len(cn_text) > CN_LEN:
        raise ValueError('CN exceeds template length')
    padded = cn_text + ('a' * (CN_LEN - len(cn_text)))
    cn_bytes = padded.encode('ascii')
    template = bytearray(_CERT_TEMPLATE_DER)
    for offset in CN_OFFSETS:
        template[offset:offset + CN_LEN] = cn_bytes
    if PY2:
        return template.tostring()
    return bytes(template)
