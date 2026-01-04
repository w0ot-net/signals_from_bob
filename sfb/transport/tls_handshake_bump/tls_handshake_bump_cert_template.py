# -*- coding: ascii -*-
"""
TLS handshake bump in-memory certificate template.
"""

from __future__ import absolute_import

import base64

from ...compat import PY2, text_type


# 256 chars raises the response payload MTU while keeping the template static.
CN_LEN = 256
CN_OFFSETS = (71, 380)

_CERT_TEMPLATE_DER_B64 = (
    b'MIIFBzCCA++gAwIBAgIUOWH8Ju2RPDYUpw+pJ9lrbkqR94YwDQYJKoZIhvcNAQELBQAwggERMYIB'
    b'DTCCAQkGA1UEAwyCAQBhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhMB4XDTI2MDEwNDIxNDYx'
    b'MFoXDTI2MDEwNTIxNDYxMFowggERMYIBDTCCAQkGA1UEAwyCAQBhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh'
    b'YWFhYWFhYWFhMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4dnc0lZi9Ff6Wuy6X2yn'
    b'8E1oMfdj2MnraRnRmbaTqynt8Y12TTO4zGWujtTBJR4meGVz8yVY1bC5Rr3o8teQ8GAOwXhQgJWN'
    b'33KFg0h8IK1YR+cG1zxFTSlGpTxpP+ILXysV2fWFsoaQ7QVvlesw8htBzAjqQWRA/dQyQopN3/rZ'
    b'zwVlEfCQRbOAswsxNSmULfsPZzatN+k/Wj0PZ8pxcIlxptn8Yxevu4/nPn4AcI/+cxD+Nsnkm8ll'
    b'G/hec7b5e+5Zo/42MgscZIrO7ErpazuQmb5UwoIa6523SvTEaI6/3sW5pgFKlYBNaU7589JAV089'
    b'QAIGgarTuJLUbgTF5QIDAQABo1MwUTAdBgNVHQ4EFgQUlWCcEVqZ3h0gjcOXIFk7cKXDc9wwHwYD'
    b'VR0jBBgwFoAUlWCcEVqZ3h0gjcOXIFk7cKXDc9wwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0B'
    b'AQsFAAOCAQEAgJyZGwPZoRCzM4Q/pMEjz7PS6cJIIkQKzvPp1Muf0SWOhGzIW5O56WWiqCGPysQU'
    b'OSUOTROzSwsamABxeH+/SFo0oOB8SvFmnHpu2NxI/gloMLgc02P7xPxVt3OEhH+SM7+n+epYr9hM'
    b'SjuehmKyz6D989mdpw4FcqhWnhzcpRY+aNylG3ajSLcU04MS2jiKaC/cWekTurgGR6eO6aap4ygt'
    b'QYUBYKSpAVB+FkkTRNzGF3g6SJTAVB1g8UIVyD/XHESdxluWgLe4lOaa/bw0eXyola96Mdp2ArPz'
    b'ErHzVg7bfszr+O1eqg5FDAMJUtwP62F0vGH0FXjNpKJiVVRriA=='
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
