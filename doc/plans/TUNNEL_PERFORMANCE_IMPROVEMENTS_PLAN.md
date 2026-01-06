# Tunnel Performance Improvements Plan

## Goal
- Remove avoidable O(n) hot-path work in the tunnel send/receive loops.
- Reduce false "data received" signals that increase Alice polling.
- Trim redundant control-message processing and decode overhead.
- Make it a protocol requirement that Bob always has room to send at least
  1 byte of segment payload in every response.

## Non-Goals
- Change transport protocols, crypto behavior, or MTU/window negotiation rules.
- Modify end-to-end test coverage or run E2E tests.
- Alter reliability semantics beyond the optimizations described here.
- Add new packet header flags.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/reliability/send_window.py
- sfb/channel/channel_manager.py
- sfb/protocol/segment.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- doc/TUNNEL.md
- doc/ASYMMETRY.md
- doc/PROTOCOL.md
- doc/DNS_TRANSPORT.md

## Plan
1) Add a data-unacked counter to SendWindow and update it in send/ack paths so Alice can query it in O(1).
2) Keep _unacked ordered by sequence (for cumulative ACK), but track oldest-by-send-time separately (min-heap or cached pointer). Use lazy validation on retransmit/ack updates; fall back to a scan only when the cache is stale.
3) In BaseTunnel packet processing, deliver control segments for each ready packet, then process control messages once per packet; keep control-before-data ordering and remove the redundant post-loop control polling.
4) Define a protocol requirement: Bob responses must always have capacity for
   at least 1 byte of segment payload. Enforce this per transport by ensuring
   the response payload cap is never less than
   PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1. If a transport cannot meet
   this floor for the configured settings, fail fast during initialization
   (config error) rather than allowing "pending data but no segment fits"
   responses.
5) Treat "real data" as the presence of segments (control or data), not the
   KEEPALIVE flag. Ack-only responses (no segments) should not trigger Alice's
   data pacing.
6) Add a data-pending event in ChannelManager (mirroring control_send_event)
   that is inclusive of control messages, so Alice can check pending state
   without repeated lock acquisition inside the hot send loop. Update it on
   register/unregister, send-state transitions, and active-channel pruning.
7) Add a fast path in BaseTunnel decode to skip Segment.decode_all when the
   decrypted body is empty.
8) Update doc/TUNNEL.md, doc/ASYMMETRY.md, doc/PROTOCOL.md, and
   doc/DNS_TRANSPORT.md to document the "real data" definition and the
   transport-level minimum payload guarantee.

## Performance/Complexity Proposals
- Use a min-heap with lazy deletion to keep oldest-unacked selection near O(log n) without reordering _unacked.
- Prefer segment-presence checks over keepalive flags for pacing decisions to reduce false "data received" signals.
- Enforce a transport-level minimum response payload so Bob can always send at
  least 1 byte of data when pending.
