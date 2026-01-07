# DNS Poll Hint Clamp Modes Plan

Status: draft

## Goal

Implement three DNS clamp scenarios with standardized clamp names:

1) clamp_safe_max_alice: default safe max (largest query payload that still
   yields a MIN_PACKET_MTU response cap).
2) clamp_balanced: poll hint with segments, using the balanced query payload.
3) clamp_max_bob: poll hint with keepalive (no segments), using the
   max-response query payload (largest query payload that yields the maximum
   response cap).

## Affected Components

- sfb/transport/dns/dns_client.py
- sfb/tunnel/bob_tunnel.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes

- Default mode uses clamp_safe_max_alice (min_response_query_payload).
- Treat POLL_HINT + HAS_SEGMENTS as clamp_balanced, using the precomputed
  balanced query payload; fallback to clamp_max_bob if no balanced point exists.
- Treat POLL_HINT + KEEPALIVE (no segments) as clamp_max_bob (largest query
  payload that yields the maximum response cap).
- Do not send control-only poll-hint responses; control segments are treated
  the same as data segments, and poll-hint keepalives are used when nothing fits.
- POLL_HINT is advisory and may be set whenever Bob needs Alice to clamp,
  regardless of the per-request response cap.

## Implementation Steps

1. DNS client: keep `send_packet_mtu` as the clamp_safe_max_alice cap (so MTU
   negotiation
   and segment sizing stay safe) and store the raw query MTU separately for
   lookup/logging; default mode uses safe max, not the raw maximum.
2. DNS client: add a poll-hint mode field so `_reset_poll_hint_budget()`
   records whether the last poll hint arrived with segments or keepalive.
3. DNS client: update `_update_bob_data_from_payload()` to set poll-hint mode
   based on content flags (POLL_HINT + HAS_SEGMENTS vs POLL_HINT + KEEPALIVE).
4. DNS client: update `_select_payload_cap()` to choose between:
   - clamp_balanced for POLL_HINT + HAS_SEGMENTS,
   - clamp_max_bob for POLL_HINT + KEEPALIVE,
   - clamp_safe_max_alice for default mode,
   and log the selected clamp mode and any fallback used.
5. Bob tunnel: remove response-cap gating for POLL_HINT (delete
   `_poll_hint_allowed`) and emit POLL_HINT whenever Bob needs Alice to clamp.
6. Bob tunnel: remove the control-only poll-hint path; when no segments fit
   but data is pending, send KEEPALIVE + POLL_HINT and leave segments queued
   for a larger-cap poll. When responding with segments, set
   POLL_HINT whenever pending data remains.
7. Update DNS_TRANSPORT.md to describe clamp_safe_max_alice, clamp_balanced,
   clamp_max_bob, and the "no control-only poll-hint segments" rule.
8. Update protocol/transport docs to remove the response-cap gating rule for
   POLL_HINT.
