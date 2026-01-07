# DNS Poll Hint Clamp Modes Plan

Status: draft

## Goal

Implement the three scenarios A/B/C with standardized clamp names:

Scenario A (Bob has nothing to send, Alice sending at max):
- Use clamp_safe_max_alice: default safe max (largest query payload that still
  yields a MIN_PACKET_MTU response cap).

Scenario B (Alice sending at max, Bob sends POLL_HINT with segments):
- Use clamp_balanced: POLL_HINT + HAS_SEGMENTS selects the balanced query
  payload when Alice has pending data (fallback to clamp_max_bob if no
  balanced point exists).

Scenario C (Alice idle but still at max, Bob sends POLL_HINT):
- Use clamp_max_bob: when Alice has no pending data, any POLL_HINT (keepalive
  or segments) selects the max-response query payload (largest query payload
  that yields the maximum response cap).

## Affected Components

- sfb/transport/dns/dns_client.py
- sfb/tunnel/bob_tunnel.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes

- Scenario A uses clamp_safe_max_alice (min_response_query_payload).
- Scenario B uses clamp_balanced (POLL_HINT + HAS_SEGMENTS) only when Alice has
  pending data; fallback to clamp_max_bob if no balanced point exists.
- Scenario C uses clamp_max_bob whenever Alice has no pending data and sees any
  POLL_HINT (KEEPALIVE or HAS_SEGMENTS).
- Do not send control-only poll-hint responses; control segments are treated
  the same as data segments, and poll-hint keepalives are used when nothing fits.
- POLL_HINT is advisory and may be set whenever Bob needs Alice to clamp,
  regardless of the per-request response cap.

## Implementation Steps

1. DNS client: keep `send_packet_mtu` as the clamp_safe_max_alice cap (so MTU
   negotiation and segment sizing stay safe), build the response-cap lookup
   against that safe-max bound, and store the raw query MTU only for logging.
   Default mode uses safe max, not the raw maximum.
2. DNS client: add a poll-hint mode field so `_reset_poll_hint_budget()`
   records whether the last poll hint arrived with segments or keepalive.
3. DNS client: update `_update_bob_data_from_payload()` to set poll-hint mode
   based on content flags (POLL_HINT + HAS_SEGMENTS vs POLL_HINT + KEEPALIVE).
4. DNS client: update `_select_payload_cap()` to choose between:
   - clamp_max_bob when POLL_HINT is active and Alice has no pending data,
   - clamp_balanced for POLL_HINT + HAS_SEGMENTS when Alice has pending data,
   - clamp_max_bob for POLL_HINT + KEEPALIVE,
   - clamp_safe_max_alice for default mode,
   and log the selected clamp mode and any fallback used.
5. Bob tunnel: emit POLL_HINT in the actual send paths:
   - in `_send_retransmit_response`, send KEEPALIVE + POLL_HINT when the
     retransmit exceeds the per-request cap.
   - remove the control-only poll-hint path; when no segments fit but data is
     pending, send KEEPALIVE + POLL_HINT and leave segments queued for a
     larger-cap poll.
6. Bob tunnel: when responding with segments, thread `pending_data` through so
   POLL_HINT is set only when more data remains.
7. Update DNS_TRANSPORT.md to describe clamp_safe_max_alice, clamp_balanced,
   clamp_max_bob, and the "no control-only poll-hint segments" rule.
8. Update protocol/transport docs to remove the response-cap gating rule for
   POLL_HINT.
