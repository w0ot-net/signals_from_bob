# DNS Adaptive Query Clamp Flags Plan

Status: draft

## Goal

Use tunnel header flags (HAS_SEGMENTS or POLL_HINT => data, KEEPALIVE => clear)
to drive the DNS adaptive query clamp state so Bob's poll hints do not clear the
bob_has_data window.

## Non-Goals

- Reintroduce FLAG_WANTS_POLL or change protocol flag definitions.
- Change tunnel retransmit, polling cadence, or asymmetry rules.
- Update or run tests in tests/e2e/.

## Affected Components

- sfb/transport/dns/dns_client.py

## Design Notes

- DNS responses carry the full tunnel packet header in plaintext, so parse
  PacketHeader without decrypting the body.
- Treat FLAG_HAS_SEGMENTS or FLAG_POLL_HINT as "Bob has data" so the clamp
  remains in balanced mode after poll-hint keepalives.
- Treat FLAG_KEEPALIVE without FLAG_POLL_HINT as "no data" and allow the
  bob_has_data window to decay.
- If the header cannot be decoded or flags are missing (handshake/invalid),
  keep the existing bob_has_data counters unchanged and log a debug event.

## Implementation Steps

1. Import PacketHeader plus FLAG_HAS_SEGMENTS/FLAG_KEEPALIVE/FLAG_POLL_HINT into
   dns_client.
2. Replace the payload-length heuristic in _handle_response with a helper that
   decodes the header and returns a data-state classification.
3. Update _update_bob_data_state to accept the derived data-state (not just a
   length-based boolean) and apply the same window/retransmit-guard logic.
4. Add a small debug log when header decoding fails or flags are unusable so
   clamp decisions remain observable.

## Validation

- Run existing non-e2e DNS transport tests with python3 if available.
- Do not run tests in tests/e2e/ (user-only).
