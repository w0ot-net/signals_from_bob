# DNS Poll Hint Clamp Modes Plan

Status: draft

## Goal

Allow Alice to send the largest safe DNS query payload by default, then
react to Bob's poll hints with a balanced clamp when he is sending segments,
and a minimum-query clamp when he can only send keepalive + poll hint. This
preserves the MIN_PACKET_MTU poll-hint rule and keeps Bob throughput bounded
by Alice polling.

## Affected Components

- sfb/transport/dns/dns_client.py
- sfb/tunnel/bob_tunnel.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes

- Keep the ASYMMETRY rule: Bob only sets POLL_HINT when the response cap can
  carry at least one segment byte (MIN_PACKET_MTU).
- Treat POLL_HINT + HAS_SEGMENTS as the "balanced" clamp, using the precomputed
  balanced query payload (fallback to the minimum-query cap if none exists).
- Treat POLL_HINT + KEEPALIVE (no segments) as the "minimum-query" clamp to
  maximize response capacity for Bob.
- Default (no poll hint) uses the largest query payload that still allows a
  MIN_PACKET_MTU response cap unless we explicitly decide to allow larger
  queries that suppress POLL_HINT.

## Implementation Steps

1. DNS client: store the unclamped query MTU and the min-response query cap;
   add a poll-hint mode field so `_reset_poll_hint_budget()` records whether
   the last poll hint came with segments or keepalive.
2. DNS client: update `_update_bob_data_from_payload()` to set poll-hint mode
   based on content flags (POLL_HINT + HAS_SEGMENTS vs POLL_HINT + KEEPALIVE).
3. DNS client: update `_select_payload_cap()` to choose between:
   - balanced query payload for POLL_HINT + HAS_SEGMENTS,
   - minimum query payload for POLL_HINT + KEEPALIVE,
   - no clamp for default mode,
   and log the selected clamp mode and any fallback used.
4. Bob tunnel: when responding with segments, set POLL_HINT whenever Bob has
   pending data (and the response cap allows it); keep the existing keepalive
   + poll-hint path when no segments fit.
5. Update DNS_TRANSPORT.md to describe the new max/balanced/min clamp behavior
   and the poll-hint content-flag mapping.
