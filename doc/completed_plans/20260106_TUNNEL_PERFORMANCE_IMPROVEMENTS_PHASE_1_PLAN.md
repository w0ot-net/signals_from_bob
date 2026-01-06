# Tunnel Performance Improvements Phase 1 Plan

## Goal
- Remove avoidable O(n) hot-path work in SendWindow by adding O(1) queries for data-unacked state.
- Improve retransmit selection by tracking oldest-by-send-time without disturbing cumulative ACK ordering.

## Non-Goals
- Change transport protocols, crypto behavior, or MTU/window negotiation rules.
- Modify end-to-end test coverage or run E2E tests.
- Alter reliability semantics beyond the SendWindow optimizations described here.
- Add new packet header flags.

## Affected Components
- sfb/reliability/send_window.py
- sfb/tunnel/alice_tunnel.py

## Plan
1) Add a data-unacked counter to SendWindow and update it in send/ack paths so Alice can query it in O(1).
2) Preserve _unacked insertion order (cumulative ACK scanning assumes send order with wrap-aware comparisons). Track oldest-by-send-time separately (min-heap or cached pointer) without reordering _unacked. Invalidate cached/heap entries on retransmit/ack/drop (including Bob's opportunistic retransmits), and use lazy validation; fall back to a scan only when the cache is stale.

## Performance/Complexity Proposals
- Use a min-heap with lazy deletion to keep oldest-unacked selection near O(log n) without reordering _unacked (avoid wrap-related cumulative ACK regressions). Prefer this over a cached pointer so we avoid repeated O(n) scans when retransmitting the oldest in tight poll loops.

## Execution Notes
- 2026-01-06: Implemented the SendWindow data-unacked counter and heap-backed oldest selection. Tests not run (per instructions).
