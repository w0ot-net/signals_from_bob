# DNS Poll Hint Clamp Modes Plan

Status: draft

## Goal

Implement three DNS clamp scenarios while keeping the MIN_PACKET_MTU rule:

1) Default: Alice uses the largest query payload that still allows a
   MIN_PACKET_MTU response cap ("safe max").
2) Poll hint with segments: Alice clamps to a balanced query payload.
3) Poll hint with keepalive (no segments): Alice clamps to the minimum
   query payload to maximize Bob's response cap.

## Affected Components

- sfb/transport/dns/dns_client.py
- sfb/tunnel/bob_tunnel.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes

- Default mode uses the "safe max" query payload (min_response_query_payload).
- Treat POLL_HINT + HAS_SEGMENTS as the "balanced" clamp, using the precomputed
  balanced query payload; fallback to the minimum-query clamp if no balanced
  point exists.
- Treat POLL_HINT + KEEPALIVE (no segments) as the minimum-query clamp to
  maximize response capacity for Bob.
- Do not send control-only poll-hint responses; control segments are treated
  the same as data segments, and poll-hint keepalives are used when nothing fits.
- POLL_HINT is advisory and may be set whenever Bob needs Alice to clamp,
  regardless of the per-request response cap.

## Implementation Steps

1. DNS client: keep `send_packet_mtu` as the safe-max cap (so MTU negotiation
   and segment sizing stay safe) and store the raw query MTU separately for
   lookup/logging; default mode uses safe max, not the raw maximum.
2. DNS client: add a poll-hint mode field so `_reset_poll_hint_budget()`
   records whether the last poll hint arrived with segments or keepalive.
3. DNS client: update `_update_bob_data_from_payload()` to set poll-hint mode
   based on content flags (POLL_HINT + HAS_SEGMENTS vs POLL_HINT + KEEPALIVE).
4. DNS client: update `_select_payload_cap()` to choose between:
   - balanced query payload for POLL_HINT + HAS_SEGMENTS,
   - minimum query payload for POLL_HINT + KEEPALIVE,
   - safe-max query payload for default mode,
   and log the selected clamp mode and any fallback used.
5. Bob tunnel: remove response-cap gating for POLL_HINT (delete
   `_poll_hint_allowed`) and emit POLL_HINT whenever Bob needs Alice to clamp.
6. Bob tunnel: remove the control-only poll-hint path; when no segments fit
   but data is pending, send KEEPALIVE + POLL_HINT and leave segments queued
   for a larger-cap poll. When responding with segments, set
   POLL_HINT whenever pending data remains.
7. Update DNS_TRANSPORT.md to describe the safe-max default, balanced/min
   clamps, and the "no control-only poll-hint segments" rule.
8. Update protocol/transport docs to remove the response-cap gating rule for
   POLL_HINT.
