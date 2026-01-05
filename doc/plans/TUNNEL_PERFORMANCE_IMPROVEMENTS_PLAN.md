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

## Plan
1) Add a data-unacked counter to SendWindow and update it in send/ack paths so Alice can query it in O(1).
2) Make oldest-unacked selection O(1) by tracking the oldest packet explicitly (or using OrderedDict order) while preserving retransmit-age semantics; fall back to a scan only if the cached entry is stale.
3) In BaseTunnel packet processing, deliver all control segments, then process control messages once per packet to avoid duplicate control polling.
4) Treat "real data" as the presence of segments instead of the KEEPALIVE flag so ack-only responses do not trigger Alice's data pacing.
5) Add a data-pending event in ChannelManager (mirroring control_send_event) so Alice can check pending state without repeated lock acquisition inside the hot send loop.
6) Add a fast path in BaseTunnel decode to skip Segment.decode_all when the decrypted body is empty.
7) Update doc/TUNNEL.md to document the "real data" definition and any pending-state behavior changes.
