# Tunnel Performance Improvements Plan

## Goal
- Remove avoidable O(n) hot-path work in the tunnel send/receive loops.
- Reduce false "data received" signals that increase Alice polling.
- Trim redundant control-message processing and decode overhead.
- Make it a protocol requirement that when Bob advertises pending data
  (POLL_HINT), the response has room to send at least 1 byte of segment
  payload. Transports with per-request caps may still emit smaller responses,
  but must not set POLL_HINT unless the cap meets the minimum.

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
2) Preserve _unacked insertion order (cumulative ACK scanning assumes send order with wrap-aware comparisons). Track oldest-by-send-time separately (min-heap or cached pointer) without reordering _unacked. Invalidate cached/heap entries on retransmit/ack/drop (including Bob's opportunistic retransmits), and use lazy validation; fall back to a scan only when the cache is stale.
3) In BaseTunnel packet processing, deliver control segments for each ready packet, then process control messages once per packet; keep control-before-data ordering and remove the redundant post-loop control polling.
4) Define a protocol requirement: when Bob sends POLL_HINT, the response must
   have capacity for at least 1 byte of segment payload. Enforce this per
   transport by ensuring the response payload cap is never less than
   PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1 when POLL_HINT is emitted.
   Transports with per-request caps (DNS) may produce smaller responses, but
   must not set POLL_HINT unless the cap meets this floor. If a transport can
   never reach this floor for the configured settings, fail fast during
   initialization (config error). With this guarantee, remove ack-only
   responses: Bob must never emit empty packets without KEEPALIVE, and
   receivers treat any empty/non-KEEPALIVE packet as a protocol violation.
5) Treat "real data" as the presence of segments (control or data), not the
   KEEPALIVE flag. Empty responses are idle keepalives only.
6) Add a data-pending event in ChannelManager (mirroring control_send_event)
   that is inclusive of control messages, so Alice can check pending state
   without repeated lock acquisition inside the hot send loop. Ensure control
   send-event set/clear transitions update the combined event (or keep
   separate events and OR them in the hot loop) so the signal clears when
   control data drains. Update it on register/unregister, send-state
   transitions, and active-channel pruning.
7) Add a fast path in BaseTunnel decode to skip Segment.decode_all when the
   decrypted body is empty.
8) Update doc/TUNNEL.md, doc/ASYMMETRY.md, doc/PROTOCOL.md, and
   doc/DNS_TRANSPORT.md to document the "real data" definition, the minimum
   payload guarantee, and the removal of ack-only responses.

## Performance/Complexity Proposals
- Use a min-heap with lazy deletion to keep oldest-unacked selection near O(log n) without reordering _unacked (avoid wrap-related cumulative ACK regressions).
- Prefer segment-presence checks over keepalive flags for pacing decisions to reduce false "data received" signals.
- Enforce a transport-level minimum response payload for POLL_HINT responses
  so Bob can always send at least 1 byte of data when advertising pending.
