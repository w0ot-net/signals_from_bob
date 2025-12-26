# -*- coding: ascii -*-
"""
End-to-end tests against an authoritative DNS setup (non-direct mode).

These tests assume the domain is delegated to Bob's DNS server and that
queries flow through a recursive resolver rather than directly to Bob.
"""

from __future__ import absolute_import

import os
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest

from sfb.config import Config
from sfb.crypto import Plain
from sfb.transport.dns import DnsClient
from sfb.transport.dns import codec
from sfb.tunnel import AliceTunnel, TunnelState
from sfb.modules.file_transfer import FileTransferModule


TEST_DOMAIN = 'ebaysso.com'
TEST_BOB_IP = '149.28.195.216'
TEST_PORT = 53
REMOTE_TEST_FILE = 'sfb_e2e_roundtrip.bin'
DEBUG_DNS = False


class DnsAuthoritativeE2ETest(unittest.TestCase):
    """End-to-end tests with real DNS (authoritative mode)."""

    @classmethod
    def setUpClass(cls):
        """Create test directories."""
        cls.test_dir = tempfile.mkdtemp(prefix='sfb_dns_auth_')
        cls.local_root = os.path.join(cls.test_dir, 'alice')
        try:
            os.makedirs(cls.local_root)
        except OSError:
            pass

    @classmethod
    def tearDownClass(cls):
        """Clean up test directories."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        """Set up test fixtures."""
        self.alice_tunnel = None
        self.alice_file_module = None
        self.alice_runner = None

    def tearDown(self):
        """Clean up after each test."""
        if self.alice_runner:
            self.alice_runner.stop()

        if self.alice_file_module:
            try:
                self.alice_file_module.shutdown()
            except Exception:
                pass

        if self.alice_tunnel:
            try:
                self.alice_tunnel.close()
            except Exception:
                pass

        time.sleep(0.1)

    def _create_config(self):
        """Create config for authoritative DNS tests."""
        resolver = os.environ.get('SFB_DNS_RESOLVER')
        if resolver and ':' in resolver:
            host, port = resolver.rsplit(':', 1)
            if port != '53':
                raise ValueError('SFB_DNS_RESOLVER must use port 53')
            resolver = '%s:%s' % (host, port)
        return Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver=resolver,
            dns_listen_addr='0.0.0.0:%d' % TEST_PORT,
            dns_pending_timeout=30.0,
            tunnel_idle_timeout=120.0,
            tunnel_connect_timeout=60.0,
            tunnel_keepalive_interval=1.0,
        )

    def _start_alice(self, config):
        """Start Alice client and connect."""
        transport_cls = _DebugDnsClient if DEBUG_DNS else DnsClient
        transport = transport_cls(config)
        self.alice_tunnel = AliceTunnel(transport, config, crypto=Plain())
        self.alice_tunnel.connect(timeout=60.0)

        self.alice_runner = _TunnelRunner(self.alice_tunnel)
        self.alice_runner.start()

        self.alice_file_module = FileTransferModule(self.alice_tunnel)

    def _probe_resolver(self, resolver):
        """Probe resolver for basic reachability and NOERROR response."""
        query_id = int(time.time() * 1000) & 0xFFFF
        query_name = codec.encode_query_name(b'', TEST_DOMAIN, query_id)
        qname = codec.encode_name(query_name)
        header = struct.pack('>HHHHHH',
            query_id,
            codec.FLAG_RD,
            1,  # QDCOUNT
            0,  # ANCOUNT
            0,  # NSCOUNT
            0,  # ARCOUNT
        )
        question = qname + struct.pack('>HH', codec.QTYPE_TXT, codec.QCLASS_IN)
        packet = header + question

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(2.0)
            sock.sendto(packet, resolver)
            data, _ = sock.recvfrom(512)
        except socket.timeout:
            return None
        finally:
            sock.close()

        if len(data) < 12:
            return None
        resp_id, flags = struct.unpack('>HH', data[:4])
        if resp_id != query_id:
            return None
        return flags & codec.RCODE_MASK

    def test_put_get_roundtrip(self):
        """Upload and download a file through authoritative DNS."""
        config = self._create_config()
        resolver_probe = DnsClient(config)
        resolver = resolver_probe._resolver
        resolver_probe.close()

        rcode = self._probe_resolver(resolver)
        if rcode is None:
            self.skipTest('DNS resolver %s did not answer probe' % (resolver,))
        if rcode != codec.RCODE_NOERROR:
            self.skipTest('DNS resolver %s returned rcode=%d' % (resolver, rcode))

        self._start_alice(config)

        self.assertEqual(self.alice_tunnel._state, TunnelState.CONNECTED)

        payload = b'authoritative dns e2e'
        local_path = os.path.join(self.local_root, 'upload.bin')
        with open(local_path, 'wb') as handle:
            handle.write(payload)

        self.alice_file_module.put(local_path, REMOTE_TEST_FILE, timeout=60.0)

        download_path = os.path.join(self.local_root, 'download.bin')
        self.alice_file_module.get(REMOTE_TEST_FILE, download_path, timeout=60.0)

        with open(download_path, 'rb') as handle:
            downloaded = handle.read()
        self.assertEqual(downloaded, payload)


class _TunnelRunner(object):
    """Runs tunnel tick loop in background thread."""

    def __init__(self, tunnel):
        self._tunnel = tunnel
        self._stop = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop and self._tunnel._state == TunnelState.CONNECTED:
            try:
                self._tunnel.tick()
            except Exception:
                pass
            time.sleep(0.001)


class _DebugDnsClient(DnsClient):
    """DnsClient with minimal debug output for resolver and rcodes."""

    def __init__(self, config):
        super(_DebugDnsClient, self).__init__(config)
        resolver = self._resolver
        print('dns resolver=%s' % (resolver,))
        if config.dns_resolver is None:
            resolvers = self._load_system_resolvers()
            print('dns system resolvers=%s' % (resolvers,))

    def _parse_response(self, data):
        if len(data) >= 12:
            query_id, flags = struct.unpack('>HH', data[:4])
            rcode = flags & codec.RCODE_MASK
            if rcode != codec.RCODE_NOERROR:
                print('dns rcode=%d id=%d' % (rcode, query_id))
        return DnsClient._parse_response(self, data)


if __name__ == '__main__':
    unittest.main()
