# -*- coding: ascii -*-
"""Integration tests for lossy file transfer over in-memory transport."""

from __future__ import absolute_import

import os
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.crypto import Plain
from sfb.modules.file_transfer import FileTransferModule
from sfb.transport import (
    LossyTransport,
    NetworkImpairment,
    chaos,
    create_inmemory_transport_pair,
)
from sfb.transport.lossy import _ImpairmentEngine
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_TEST_FILE_PATH = os.path.join(_ROOT_DIR, 'test_download_files', '1MB.bin')
_MIN_TEST_FILE_SIZE = 1 * 1024 * 1024
_TMP_DIR = '/tmp'


def _tmp_available():
    if os.name == 'nt':
        return False
    return os.path.isdir(_TMP_DIR) and os.access(_TMP_DIR, os.W_OK)


def _test_file_available():
    if not os.path.isfile(_TEST_FILE_PATH):
        return False
    return os.path.getsize(_TEST_FILE_PATH) >= _MIN_TEST_FILE_SIZE


def _make_config():
    config = Config()
    config.tunnel_connect_timeout = 30.0
    config.tunnel_no_response_timeout = 600.0
    config.tunnel_idle_timeout = 600.0
    config.tunnel_keepalive_interval = 0.2
    config.tunnel_bob_poll_interval = 0.05
    config.tunnel_bob_poll_interval_bg = 0.005
    config.tunnel_tick_sleep = 0.0001
    config.tunnel_poll_pacing_enabled = False
    config.tunnel_poll_min_interval = 0.0001
    config.tunnel_poll_max_interval = 0.05
    config.tunnel_retransmit_cap = 4
    config.max_in_flight = 256
    config.tunnel_initial_window = 32
    config.file_transfer_chunk_size = 32768
    config.channel_max_send_buf = 4 * 1024 * 1024
    config.channel_max_recv_buf = 4 * 1024 * 1024
    config.protocol_initial_rto_ms = 400
    config.protocol_min_rto_ms = 100
    config.protocol_max_rto_ms = 2000
    return config


def _apply_lossy_transport_impairment(lossy, send_impairment, recv_impairment):
    lossy._send_imp = send_impairment
    lossy._recv_imp = recv_impairment
    lossy._send_engine = _ImpairmentEngine(
        send_impairment,
        stats_enabled=lossy._stats_enabled,
    )
    if recv_impairment is send_impairment:
        lossy._recv_engine = lossy._send_engine
    else:
        lossy._recv_engine = _ImpairmentEngine(
            recv_impairment,
            stats_enabled=lossy._stats_enabled,
        )


@unittest.skipUnless(
    _tmp_available() and _test_file_available(),
    'requires /tmp and test_download_files/1MB.bin',
)
class LossyInMemoryFileTransferIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._alice_transport = None
        self._bob_transport = None
        self._alice = None
        self._bob = None
        self._alice_file = None
        self._bob_file = None
        self._dest_path = None
        self._orig_handle_get_ok = None
        self._chaos_applied = False
        self._send_impairment = None
        self._no_impairment = None
        self._config = _make_config()
        self._dest_path = os.path.join(
            _TMP_DIR,
            'sfb_lossy_1mb_%d.bin' % os.getpid(),
        )
        if os.path.exists(self._dest_path):
            os.unlink(self._dest_path)

        client, server = create_inmemory_transport_pair(
            self._config,
            send_packet_mtu=4096,
            recv_packet_mtu=4096,
        )

        handshake_impairment = NetworkImpairment()
        self._no_impairment = NetworkImpairment()
        self._send_impairment = chaos(seed=7)

        self._alice_transport = LossyTransport(
            client,
            send_impairment=handshake_impairment,
            recv_impairment=handshake_impairment,
            pending_timeout_sec=1.0,
        )
        self._bob_transport = server

        self._alice = AliceTunnel(self._alice_transport, self._config, crypto=Plain())
        self._bob = BobTunnel(self._bob_transport, self._config, crypto=Plain())
        self._alice_file = FileTransferModule(self._alice)
        self._bob_file = FileTransferModule(self._bob)
        self._bob.allow_message_type(FileTransferModule.TYPE)

        self._bob.start_background()
        self._alice.connect(timeout=self._config.tunnel_connect_timeout)
        self.assertEqual(self._alice.state, TunnelState.CONNECTED)
        # Delay chaos until get_ok to avoid control handshake flakiness.
        self._orig_handle_get_ok = self._alice_file.handle_get_ok

        def _handle_get_ok_with_chaos(msg):
            self._orig_handle_get_ok(msg)
            if not self._chaos_applied:
                _apply_lossy_transport_impairment(
                    self._alice_transport,
                    self._send_impairment,
                    self._no_impairment,
                )
                self._chaos_applied = True

        self._alice_file.handle_get_ok = _handle_get_ok_with_chaos
        self._alice.start_background()
        time_provider.sleep(0.05)

    def tearDown(self):
        if self._orig_handle_get_ok is not None and self._alice_file is not None:
            self._alice_file.handle_get_ok = self._orig_handle_get_ok
            self._orig_handle_get_ok = None
        if self._alice_file is not None:
            self._alice_file.shutdown()
        if self._bob_file is not None:
            self._bob_file.shutdown()
        if self._alice is not None:
            self._alice.stop_background()
            self._alice.close()
        if self._bob is not None:
            self._bob.stop_background()
            self._bob.close()
        if self._dest_path and os.path.exists(self._dest_path):
            os.unlink(self._dest_path)

    def test_get_1mb_to_tmp_over_chaos(self):
        timeout = 300.0
        self._alice_file.get(_TEST_FILE_PATH, self._dest_path, timeout=timeout)
        self.assertTrue(os.path.exists(self._dest_path))
        self.assertEqual(
            os.path.getsize(self._dest_path),
            os.path.getsize(_TEST_FILE_PATH),
        )


if __name__ == '__main__':
    unittest.main()
