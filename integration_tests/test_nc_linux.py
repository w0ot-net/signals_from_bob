# -*- coding: ascii -*-
"""Integration tests for nc_linux module."""

from __future__ import absolute_import

import os
import tempfile
import time
import unittest

from sfb.config import Config
from sfb.transport import create_inmemory_transport_pair
from sfb.tunnel.alice_tunnel import AliceTunnel
from sfb.tunnel.bob_tunnel import BobTunnel
from sfb.modules.nc_linux.nc_linux import NcLinuxModule, _is_linux


@unittest.skipUnless(_is_linux(), 'linux only')
class NcLinuxIntegrationTests(unittest.TestCase):
    def test_pipe_to_file(self):
        config = Config()
        config.tunnel_bob_poll_interval = 0.05
        config.tunnel_bob_poll_interval_bg = 0.01
        config.tunnel_connect_timeout = 2.0
        config.nc_linux_bind_timeout = 2.0

        client, server = create_inmemory_transport_pair(config)
        alice = AliceTunnel(client, config)
        bob = BobTunnel(server, config)

        alice_mod = NcLinuxModule(alice)
        bob_mod = NcLinuxModule(bob)
        bob.allow_message_type(NcLinuxModule.TYPE)

        rfd, wfd = os.pipe()
        temp_dir = tempfile.mkdtemp()
        path = os.path.join(temp_dir, 'nc_linux_out.txt')

        try:
            bob.start_background()
            alice.connect(timeout=1.0)
            alice.start_background()

            conn = bob_mod.bind(
                remote_spec=path,
                local_spec=str(rfd),
                timeout=1.0,
            )

            os.write(wfd, b'ping')

            deadline = time.time() + 2.0
            data = b''
            while time.time() < deadline:
                try:
                    with open(path, 'rb') as handle:
                        data = handle.read()
                except Exception:
                    data = b''
                if data:
                    break
                time.sleep(0.05)

            self.assertEqual(data, b'ping')
            os.close(wfd)
            self.assertTrue(conn.wait(timeout=2.0))
        finally:
            try:
                os.close(rfd)
            except Exception:
                pass
            try:
                os.unlink(path)
            except Exception:
                pass
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass
            bob_mod.shutdown()
            alice_mod.shutdown()
            alice.stop_background()
            bob.stop_background()
            alice.close()
            bob.close()


if __name__ == '__main__':
    unittest.main()
