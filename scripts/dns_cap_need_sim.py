# -*- coding: ascii -*-
"""
Local DNS direct-mode simulation demonstrating cap_need/cap_clear.

Runs Alice and Bob against the in-process DNS transport on port 5353,
drops Bob's first data response to force a retransmit, then forces a
long-qname poll from Alice so Bob emits cap_need. Alice responds with
header-only polls, Bob retransmits and clears the cap latch.
"""

from __future__ import absolute_import

import logging
import threading
import time

from sfb.config import Config
from sfb.crypto import Plain
from sfb.transport.dns import DnsClient, DnsServer
from sfb.tunnel import AliceTunnel, BobTunnel, TunnelState


LOG = logging.getLogger(__name__)


class DroppingDnsClient(DnsClient):
    """DNS client that can drop exactly one response for loss simulation."""

    def __init__(self, config):
        super(DroppingDnsClient, self).__init__(config)
        self._drop_next = False

    def drop_next_response(self):
        """Drop the next DNS response received."""
        self._drop_next = True

    def recv(self, timeout=None):
        corr_id, data = super(DroppingDnsClient, self).recv(timeout=timeout)
        if self._drop_next and corr_id is not None:
            LOG.info('Dropping DNS response corr_id=%s bytes=%s',
                     corr_id, len(data) if data is not None else None)
            self._drop_next = False
            return (None, None)
        return (corr_id, data)


def make_config():
    """Create a config for local DNS direct mode on port 5353."""
    return Config(
        dns_base_domain='test.local',
        dns_resolver='127.0.0.1:5353',
        dns_listen_addr='127.0.0.1:5353',
        tunnel_idle_timeout=30.0,
        tunnel_connect_timeout=5.0,
        tunnel_keepalive_interval=0.5,
    )


def run_simulation():
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    config = make_config()

    # Start Bob
    bob_transport = DnsServer(config)
    bob = BobTunnel(bob_transport, config, crypto=Plain())

    def bob_loop():
        bob.serve_forever()

    bob_thread = threading.Thread(target=bob_loop, daemon=True)
    bob_thread.start()
    time.sleep(0.1)

    # Start Alice
    alice_transport = DroppingDnsClient(config)
    alice = AliceTunnel(alice_transport, config, crypto=Plain())
    alice.connect(timeout=5.0)
    LOG.info('Alice connected: state=%s', alice.state)
    assert alice.state == TunnelState.CONNECTED

    # Prepare Bob-side data to send to Alice.
    bob_channel = bob.channel_manager.open_channel()
    bob_payload = b'B' * 180
    bob_channel.write(bob_payload)
    LOG.info('Bob queued %d bytes to send to Alice', len(bob_payload))

    # Drop the first response so the packet remains unacked.
    alice_transport.drop_next_response()

    # First poll: Bob will send data, Alice will drop the response.
    alice.tick()
    time.sleep(0.2)

    # Force a long-qname poll by sending sizeable data from Alice to Bob.
    alice_channel = alice.channel_manager.open_channel()
    alice_channel.write(b'A' * 220)
    LOG.info('Alice queued long-qname data toward Bob to shrink cap')

    # Poll again: Bob tries retransmit under a reduced cap and should emit cap_need.
    alice.tick()
    time.sleep(0.2)

    # Subsequent polls should be header-only until cap_clear arrives.
    for _ in range(3):
        alice.tick()
        time.sleep(0.2)

    LOG.info('Final cap_need_active=%s unacked=%d',
             getattr(alice, '_cap_need_active', None),
             bob._send_window.unacked_count)

    # Clean shutdown
    alice.close()
    bob.close()
    bob_thread.join(timeout=1.0)


if __name__ == '__main__':
    run_simulation()
