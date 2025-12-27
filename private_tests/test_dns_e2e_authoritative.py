# -*- coding: ascii -*-
"""
End-to-end tests against an authoritative DNS setup (non-direct mode).

These tests assume the domain is delegated to Bob's DNS server and that
queries flow through a recursive resolver rather than directly to Bob.
"""

from __future__ import absolute_import

import os
import sys
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sfb.config import Config
from sfb.crypto import Plain
from sfb.transport.dns import DnsClient
from sfb.transport.dns import DnsServer
from sfb.transport.dns import codec
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState
from sfb.protocol import Packet, FLAG_SYN
from sfb.modules.file_transfer import FileTransferModule


TEST_DOMAIN = 'ebaysso.com'
TEST_BOB_IP = '149.28.195.216'
TEST_PORT = 53
REMOTE_TEST_FILE = 'sfb_e2e_roundtrip.bin'
DEBUG_DNS = False
PROGRESS_INTERVAL = 5.0
DEFAULT_FILE_KB = 1024


def _extract_file_kb():
    """Extract optional --file-kb from argv without breaking unittest."""
    args = []
    file_kb = DEFAULT_FILE_KB
    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--file-kb' and i + 1 < len(sys.argv):
            try:
                file_kb = int(sys.argv[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        args.append(arg)
        i += 1
    sys.argv[:] = args
    return file_kb


FILE_KB = _extract_file_kb()


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
        self.bob_transport = None
        self.bob_tunnel = None
        self.bob_file_module = None
        self.bob_thread = None
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

        if self.bob_tunnel:
            try:
                self.bob_tunnel.close()
            except Exception:
                pass

        if self.bob_transport:
            try:
                self.bob_transport.close()
            except Exception:
                pass

        if self.bob_thread and self.bob_thread.is_alive():
            self.bob_thread.join(timeout=2.0)

        time.sleep(0.1)

    def _create_config(self, listen_addr=None):
        """Create config for authoritative DNS tests."""
        resolver = None
        return Config(
            dns_base_domain=TEST_DOMAIN,
            dns_resolver=resolver,
            dns_listen_addr=listen_addr or '0.0.0.0:%d' % TEST_PORT,
            dns_pending_timeout=30.0,
            tunnel_idle_timeout=120.0,
            tunnel_connect_timeout=60.0,
            tunnel_keepalive_interval=1.0,
            tunnel_timeout_packets=200,
            tunnel_max_in_flight=64,
            dns_max_pending=64,
        )

    def _start_bob(self, config):
        """Start Bob server on the authoritative address."""
        try:
            server_cls = _DebugDnsServer if DEBUG_DNS else DnsServer
            self.bob_transport = server_cls(config)
        except Exception as e:
            self.skipTest('Bob DNS server failed to bind: %s' % (e,))

        self.bob_tunnel = BobTunnel(self.bob_transport, config, crypto=Plain())
        self.bob_file_module = FileTransferModule(self.bob_tunnel)

        orig_dir = os.getcwd()

        def serve():
            os.chdir(self.local_root)
            try:
                self.bob_tunnel.serve_forever()
            finally:
                os.chdir(orig_dir)

        self.bob_thread = threading.Thread(target=serve, daemon=True)
        self.bob_thread.start()
        time.sleep(0.1)
        print('bob listen addr=%s base_domain=%s' % (
            config.dns_listen_addr, config.dns_base_domain
        ))

    def _start_alice(self, config):
        """Start Alice client and connect."""
        transport_cls = _DebugDnsClient if DEBUG_DNS else _TraceDnsClient
        transport = transport_cls(config)
        self.alice_tunnel = AliceTunnel(transport, config, crypto=Plain())
        self.alice_tunnel.connect(timeout=60.0)

        self.alice_file_module = FileTransferModule(self.alice_tunnel)
        self.alice_runner = _TunnelRunner(
            self.alice_tunnel,
            self.alice_file_module,
            progress_interval=PROGRESS_INTERVAL,
        )
        self.alice_runner.start()

    def _probe_target(self, target, label):
        """Probe a DNS target for basic reachability and NOERROR response."""
        query_id = int(time.time() * 1000) & 0xFFFF
        syn_packet = Packet(seq=1, ack=0, sack=0, flags=FLAG_SYN)
        query_name = codec.encode_query_name(syn_packet.encode(), TEST_DOMAIN, query_id)
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
            sock.settimeout(5.0)
            print('dns probe target=%s qname=%s' % (label, query_name))
            sock.sendto(packet, target)
            data, _ = sock.recvfrom(512)
        except socket.timeout:
            print('dns probe timeout target=%s' % (label,))
            return None
        finally:
            sock.close()

        if len(data) < 12:
            if DEBUG_DNS:
                print('dns probe short response len=%d' % (len(data),))
            return None
        resp_id, flags = struct.unpack('>HH', data[:4])
        if resp_id != query_id:
            print('dns probe id mismatch target=%s got=%d expected=%d' % (
                label, resp_id, query_id
            ))
            return None
        rcode = flags & codec.RCODE_MASK
        aa = bool(flags & codec.FLAG_AA)
        ra = bool(flags & codec.FLAG_RA)
        rd = bool(flags & codec.FLAG_RD)
        print('dns probe flags target=%s flags=0x%04x rcode=%d aa=%s ra=%s rd=%s' % (
            label, flags, rcode, aa, ra, rd
        ))
        return rcode

    def test_get_1kb_file(self):
        """Download a 1KB file through authoritative DNS."""
        bob_config = self._create_config(
            listen_addr='%s:%d' % (TEST_BOB_IP, TEST_PORT)
        )
        self._start_bob(bob_config)

        config = self._create_config()
        resolver_probe = DnsClient(config)
        resolver = resolver_probe._resolver
        resolver_probe.close()

        print('alice base_domain=%s resolver=%s' % (
            config.dns_base_domain, resolver
        ))
        self._warn_if_loopback(resolver)

        direct_rcode = self._probe_target(
            (TEST_BOB_IP, TEST_PORT), 'bob-direct'
        )
        if direct_rcode is None:
            print('warning: no direct response from bob on port 53')

        rcode = self._probe_target(resolver, 'recursive')
        if rcode is None:
            self.skipTest('DNS resolver %s did not answer probe' % (resolver,))
        if rcode != codec.RCODE_NOERROR:
            self.skipTest('DNS resolver %s returned rcode=%d' % (resolver, rcode))

        self._start_alice(config)

        self.assertEqual(self.alice_tunnel._state, TunnelState.CONNECTED)

        payload = b'A' * (FILE_KB * 1024)
        remote_path = os.path.join(self.local_root, REMOTE_TEST_FILE)
        with open(remote_path, 'wb') as handle:
            handle.write(payload)

        download_path = os.path.join(self.local_root, 'download.bin')
        self.alice_file_module.get(REMOTE_TEST_FILE, download_path, timeout=300.0)

        with open(download_path, 'rb') as handle:
            downloaded = handle.read()
        self.assertEqual(downloaded, payload)
        if self.alice_file_module.last_stats:
            print('transfer stats: %s' % (self.alice_file_module.last_stats,))

    def _warn_if_loopback(self, resolver):
        """Warn if resolver is on loopback; traffic may not appear on NIC."""
        host = resolver[0]
        if host.startswith('127.') or host in ('localhost', '::1'):
            print('warning: resolver on loopback; watch lo interface for DNS traffic')


class _TunnelRunner(object):
    """Runs tunnel tick loop in background thread."""

    def __init__(self, tunnel, file_module, progress_interval=5.0):
        self._tunnel = tunnel
        self._file_module = file_module
        self._progress_interval = progress_interval
        self._stop = False
        self._thread = None
        self._last_report = 0
        self._last_transferred = 0
        self._last_window = None

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
            self._maybe_report()
            time.sleep(0.001)

    def _maybe_report(self):
        now = time.time()
        if now - self._last_report < self._progress_interval:
            return
        self._last_report = now

        stats = self._file_module.current_stats
        if stats is None:
            return
        delta_transferred = stats.transferred - self._last_transferred
        self._last_transferred = stats.transferred

        interval = max(self._progress_interval, 0.001)
        file_rate = delta_transferred / interval / 1024.0

        window = self._tunnel._negotiated_window
        if self._last_window != window:
            print('window updated: %d' % window)
            self._last_window = window

        pending = None
        try:
            pending = self._tunnel._transport.pending_count()
        except Exception:
            pending = None
        unacked = self._tunnel._send_window.unacked_count

        print('progress: transferred=%d rate=%.2fKBs window=%d unacked=%d pending=%s' % (
            stats.transferred, file_rate, window, unacked, pending
        ))
        self._maybe_report_dns()

    def _maybe_report_dns(self):
        transport = self._tunnel._transport
        if not hasattr(transport, 'stats_snapshot'):
            return
        sent, received, timeouts, last_send_age, last_recv_age = (
            transport.stats_snapshot()
        )
        print('dns stats: sent=%d recv=%d timeouts=%d last_send=%.2fs last_recv=%.2fs' % (
            sent, received, timeouts, last_send_age, last_recv_age
        ))


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

    def recv(self, timeout=None):
        result = DnsClient.recv(self, timeout)
        corr_id, data = result
        if corr_id is not None and DEBUG_DNS:
            data_len = len(data) if data is not None else -1
            print('dns client recv corr=%s len=%d' % (corr_id, data_len))
        return result


class _TraceDnsClient(DnsClient):
    """DnsClient with lightweight stats for sends/receives/timeouts."""

    def __init__(self, config):
        super(_TraceDnsClient, self).__init__(config)
        self._sent = 0
        self._received = 0
        self._timeouts = 0
        now = time.time()
        self._last_send = now
        self._last_recv = now

    def send(self, data):
        self._sent += 1
        self._last_send = time.time()
        return DnsClient.send(self, data)

    def recv(self, timeout=None):
        corr_id, data = DnsClient.recv(self, timeout)
        if corr_id is None:
            if timeout is not None:
                self._timeouts += 1
            return (corr_id, data)
        self._received += 1
        self._last_recv = time.time()
        return (corr_id, data)

    def stats_snapshot(self):
        now = time.time()
        last_send_age = now - self._last_send
        last_recv_age = now - self._last_recv
        return (self._sent, self._received, self._timeouts,
                last_send_age, last_recv_age)

class _DebugDnsServer(DnsServer):
    """DnsServer with minimal debug output for queries and responses."""

    def recv(self, timeout=None):
        self._sock.settimeout(timeout)

        while True:
            try:
                pkt_data, client_addr = self._sock.recvfrom(max(self._edns_size, 4096))
            except socket.timeout:
                return None, None
            except socket.error as e:
                raise Exception('Receive failed: %s' % e)

            try:
                query_id, qname, qtype = self._parse_query(pkt_data)
            except (ValueError, Exception) as e:
                print('dns server parse error: %s' % (e,))
                continue

            if DEBUG_DNS:
                print('dns server query id=%d qtype=%d qname=%s from=%s' % (
                    query_id, qtype, qname, client_addr
                ))

            if not qname.lower().endswith('.' + self._base_domain):
                if DEBUG_DNS:
                    print('dns server ignore: domain mismatch')
                continue

            if qtype not in (codec.QTYPE_TXT, codec.QTYPE_NULL):
                if DEBUG_DNS:
                    print('dns server ignore: qtype %d' % (qtype,))
                self._send_empty_response(query_id, qname, qtype, client_addr)
                continue

            try:
                data = codec.decode_query_name(qname, self._base_domain)
            except ValueError as e:
                if DEBUG_DNS:
                    print('dns server decode error: %s' % (e,))
                self._send_empty_response(query_id, qname, qtype, client_addr)
                continue

            if DEBUG_DNS:
                print('dns server accept data_len=%d' % (len(data),))

            def responder(response_data, _qid=query_id, _qname=qname,
                          _qtype=qtype, _addr=client_addr):
                if DEBUG_DNS:
                    print('dns server respond len=%d to=%s' % (
                        len(response_data), _addr
                    ))
                self._send_response(_qid, _qname, _qtype, response_data, _addr)

            return data, responder


if __name__ == '__main__':
    unittest.main()
