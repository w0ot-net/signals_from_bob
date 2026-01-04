# -*- coding: ascii -*-
from __future__ import absolute_import

import base64
import struct
import unittest

from sfb.config import Config
from sfb.transport.tls_handshake_bump import tls_handshake_bump_codec as codec
from sfb.transport.tls_handshake_bump import (
    tls_handshake_bump_cert_template as cert_template,
)
from sfb.transport.tls_handshake_bump.tls_handshake_bump_client import (
    TlsHandshakeBumpClient,
)
from sfb.compat import byte_at


_TEST_CERT_DER = base64.b64decode(
    b'MIIBlzCCATGgAwIBAgIJAO7qvE4fO1oGMAoGCCqGSM49BAMCMBoxGDAWBgNV'
    b'BAMMD3Rlc3QtY2VydC1jYTAeFw0yMDAxMDEwMDAwMDBaFw0yMDAxMDIwMDAw'
    b'MDBaMBoxGDAWBgNVBAMMD3Rlc3QtY2VydC1jYTBZMBMGByqGSM49AgEGCCqG'
    b'SM49AwEHA0IABB9q9a7S1AsYp0IEr8KVcZ5Co5tcnxYk7tKx4mK9YHsw1yRu'
    b'0P4uP0QTOUOQldZCjZwqHqSqG6l9bPqG3KejUDBOMB0GA1UdDgQWBBQ5r5Vn'
    b'TSIFbA7c4XnA5Z3x9x7JrDAfBgNVHSMEGDAWgBQ5r5VnTSIFbA7c4XnA5Z3x'
    b'9x7JrDAMBgNVHRMEBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCICoC5xld9Q2x'
    b'W4sH0QpH3t8SfbU2I3f6jSxA2IACAiEAyqTqXG0tU6+Q1rXkHHrK6bD68sLB'
    b'8Q7XhVtDkN5qPjA='
)


def _unpack_u24(data):
    return struct.unpack('!I', b'\x00' + data)[0]


def _handshake_types(record):
    offset = codec.TLS_RECORD_HEADER_LEN
    types = []
    while offset < len(record):
        hs_type = byte_at(record, offset)
        hs_len = _unpack_u24(record[offset + 1:offset + 4])
        types.append(hs_type)
        offset += 4 + hs_len
    return types


class TlsHandshakeBumpClientServerTests(unittest.TestCase):
    def test_client_extracts_cn_from_error_page(self):
        client_cfg = Config(
            transport='tls_handshake_bump',
            tls_bump_base_domain='example.com',
            tls_bump_target='127.0.0.1:443',
            tls_bump_response_mode='regex',
            tls_bump_response_regex=(
                r'Self-signed SSL Certificate: /CN=([A-Za-z2-7]+)'
            ),
        )
        client = TlsHandshakeBumpClient(client_cfg)
        payload = b'ping'
        cn_value = codec.encode_cn_value(payload, max_len=cert_template.CN_LEN)
        cn_value = cn_value + ('a' * (cert_template.CN_LEN - len(cn_value)))
        body = (
            b'<html><body>Self-signed SSL Certificate: /CN=' +
            cn_value.encode('ascii') +
            b'</body></html>'
        )
        response = (
            b'HTTP/1.1 502 Bad Gateway\r\n'
            b'Content-Type: text/html\r\n'
            b'\r\n' +
            body
        )
        extracted = client._extract_payload(response, 1)
        self.assertEqual(extracted, payload)
        client.close()

    def test_build_server_handshake_record(self):
        record = codec.build_server_handshake_record(_TEST_CERT_DER,
                                                     random_bytes=b'\x01' * 32)
        record_len = codec.parse_record_header(record[:codec.TLS_RECORD_HEADER_LEN])
        self.assertEqual(len(record), codec.TLS_RECORD_HEADER_LEN + record_len)
        self.assertIn(_TEST_CERT_DER, record)
        types = _handshake_types(record)
        self.assertEqual(types, [
            codec.TLS_HANDSHAKE_SERVER_HELLO,
            codec.TLS_HANDSHAKE_CERTIFICATE,
            codec.TLS_HANDSHAKE_SERVER_HELLO_DONE,
        ])


if __name__ == '__main__':
    unittest.main()
