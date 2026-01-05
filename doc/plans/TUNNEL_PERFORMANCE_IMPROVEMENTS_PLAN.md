# Tunnel Performance Improvements Plan

## Goal
- Remove avoidable O(n) hot-path work in the tunnel send/receive loops.
- Reduce false "data received" signals that increase Alice polling.
- Trim redundant control-message processing and decode overhead.

## Non-Goals
- Change transport protocols, crypto behavior, or MTU/window negotiation rules.
- Modify end-to-end test coverage or run E2E tests.
- Alter reliability semantics beyond the optimizations described here.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/reliability/send_window.py
- sfb/channel/channel_manager.py
- sfb/protocol/segment.py
- doc/TUNNEL.md
- doc/ASYMMETRY.md

## Plan
1) Add a data-unacked counter to SendWindow and update it in send/ack paths so Alice can query it in O(1).
2) Keep _unacked ordered by sequence (for cumulative ACK), but track oldest-by-send-time separately (min-heap or cached pointer). Use lazy validation on retransmit/ack updates; fall back to a scan only when the cache is stale.
3) In BaseTunnel packet processing, deliver control segments for each ready packet, then process control messages once per packet; keep control-before-data ordering and remove the redundant post-loop control polling.
4) Treat "real data" as the presence of segments (control or data), not the KEEPALIVE flag. Ack-only responses (no segments) should not trigger Alice's data pacing. If pending data cannot fit, consider an explicit pending-data hint so Alice keeps polling.
5) Add a data-pending event in ChannelManager (mirroring control_send_event) so Alice can check pending state without repeated lock acquisition inside the hot send loop. Update it on register/unregister, send-state transitions, and active-channel pruning.
6) Add a fast path in BaseTunnel decode to skip Segment.decode_all when the decrypted body is empty.
7) Update doc/TUNNEL.md and doc/ASYMMETRY.md to document the "real data" definition, ack-only behavior, and pending-data note.

## Performance/Complexity Proposals
- Use a min-heap with lazy deletion to keep oldest-unacked selection near O(log n) without reordering _unacked.
- Prefer segment-presence checks over keepalive flags for pacing decisions to reduce false "data received" signals.
- Add a pending-data hint (control message or header bit) only if ack-only responses cause observable poll slowdown.
