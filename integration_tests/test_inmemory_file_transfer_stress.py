# -*- coding: ascii -*-
"""Integration tests for in-memory file transfer stress."""

from __future__ import absolute_import

import os
import tempfile
import unittest

from sfb import time_provider
from sfb.config import Config
from sfb.crypto import Plain
from sfb.modules.file_transfer import FileTransferModule
from sfb.transport import create_inmemory_transport_pair
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState


_TRANSFER_COUNT = 500
_DUMMY_SIZE = 4096


def _make_config():
    config = Config()
    config.tunnel_connect_timeout = 5.0
    config.tunnel_no_response_timeout = 120.0
    config.tunnel_idle_timeout = 120.0
    config.tunnel_bob_poll_interval = 0.02
    config.tunnel_bob_poll_interval_bg = 0.005
    config.tunnel_tick_sleep = 0.0005
    config.max_in_flight = 64
    config.tunnel_initial_window = 32
    config.channel_id_reuse_cooldown = 0.0
    return config


def _write_dummy_file(path, size):
    handle = open(path, 'wb')
    try:
        handle.write(b'A' * size)
    finally:
        handle.close()


def _select_sink_path(tmp_dir):
    if os.name != 'nt' and os.path.isabs(os.devnull):
        return os.devnull, False
    return os.path.join(
        tmp_dir,
        'sfb_file_transfer_sink_%d.bin' % os.getpid(),
    ), True


class InMemoryFileTransferStressTests(unittest.TestCase):
    def setUp(self):
        self._config = _make_config()
        self._tmp_dir = tempfile.gettempdir()
        self._dummy_path = os.path.join(
            self._tmp_dir,
            'sfb_file_transfer_dummy_%d.bin' % os.getpid(),
        )
        self._sink_path, self._sink_is_temp = _select_sink_path(self._tmp_dir)

        if os.path.exists(self._dummy_path):
            os.unlink(self._dummy_path)
        if self._sink_is_temp and os.path.exists(self._sink_path):
            os.unlink(self._sink_path)
        _write_dummy_file(self._dummy_path, _DUMMY_SIZE)

        client, server = create_inmemory_transport_pair(
            self._config,
            send_mtu=4096,
            recv_mtu=4096,
        )
        self._alice = AliceTunnel(client, self._config, crypto=Plain())
        self._bob = BobTunnel(server, self._config, crypto=Plain())

        self._alice_file = FileTransferModule(self._alice)
        self._bob_file = FileTransferModule(self._bob)
        self._bob.allow_message_type(FileTransferModule.TYPE)

        self._bob.start_background()
        self._alice.connect(timeout=self._config.tunnel_connect_timeout)
        self.assertEqual(self._alice.state, TunnelState.CONNECTED)
        self._alice.start_background()
        time_provider.sleep(0.05)

    def tearDown(self):
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
        if self._sink_is_temp and os.path.exists(self._sink_path):
            os.unlink(self._sink_path)
        if self._dummy_path and os.path.exists(self._dummy_path):
            os.unlink(self._dummy_path)

    def test_bidirectional_file_transfer_stress(self):
        timeout = 5.0
        for _ in range(_TRANSFER_COUNT):
            self._alice_file.get(
                self._dummy_path,
                self._sink_path,
                timeout=timeout,
            )
        for _ in range(_TRANSFER_COUNT):
            self._bob_file.get(
                self._dummy_path,
                self._sink_path,
                timeout=timeout,
            )


if __name__ == '__main__':
    unittest.main()
