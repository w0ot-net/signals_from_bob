# Tunnel Performance Improvements Phase 2 Plan

## Goal
- Trim hot-path packet processing and send-loop work without changing protocol semantics.
- Reduce redundant control processing and avoid unnecessary segment decode work.

## Non-Goals
- Change transport protocols, crypto behavior, or MTU/window negotiation rules.
- Modify end-to-end test coverage or run E2E tests.
- Alter reliability semantics beyond the optimizations described here.
- Add new packet header flags.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/channel/channel_manager.py

## Plan
1) In BaseTunnel packet processing, deliver control segments for each ready packet, then process control messages once per packet; keep control-before-data ordering and remove the redundant post-loop control polling.
2) if not already existing, add a data-pending event in ChannelManager (mirroring control_send_event) that is inclusive of control messages, so Alice can check pending state without repeated lock acquisition inside the hot send loop. Ensure control send-event set/clear transitions update the combined event (or keep separate events and OR them in the hot loop) so the signal clears when control data drains. Update it on register/unregister, send-state transitions, and active-channel pruning.
3) Add a fast path in BaseTunnel decode to skip Segment.decode_all when the decrypted body is empty.
