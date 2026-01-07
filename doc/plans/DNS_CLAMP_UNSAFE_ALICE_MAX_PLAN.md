# DNS Clamp Unsafe Alice Max Plan

Status: draft

## Goal

Add a `clamp_unsafe_alice_max` mode for DNS clamp selection that maximizes
Alice's query payload while still guaranteeing Bob can send a header-only
SFB keepalive (zero segments) with `POLL_HINT` so he can request a clamp when
needed.

## Affected Components

- sfb/transport/dns/dns_client.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes

- Keep the existing safe-max behavior (`clamp_safe_max_alice`) that guarantees
  response caps >= `MIN_PACKET_MTU` so Bob can always send at least one
  segment when the safe mode is selected.
- Define `clamp_unsafe_alice_max` as the largest query payload whose
  per-request response cap is >= `PACKET_HEADER_SIZE` (header-only keepalive).
- Only select `clamp_unsafe_alice_max` when:
  - No poll-hint budget is active, and
  - Alice has real data pending, and
  - Bob is not believed to have pending real data.
- When Bob needs space, he sends `KEEPALIVE + POLL_HINT` (no segments). This
  refreshes the poll-hint budget and switches Alice to `clamp_max_bob` on the
  next polls, restoring a response cap large enough for segments.
- If the unsafe cap cannot guarantee `PACKET_HEADER_SIZE`, fall back to
  `clamp_safe_max_alice` and log the fallback once per interval.

## Implementation Steps

1. In `sfb/transport/dns/dns_client.py`, preserve the raw query MTU and store
   the safe-max query payload separately instead of overwriting the transport
   send MTU. Compute and store the unsafe max query payload using the response
   cap lookup with a `PACKET_HEADER_SIZE` target.
2. Extend `_select_payload_cap()` to add the `clamp_unsafe_alice_max` mode
   under the conditions above; clamp the selected payload to the min query MTU
   and the raw query MTU. Keep existing poll-hint and retransmit-guard
   priorities unchanged.
3. Update clamp-selection logging to include the unsafe max value and any
   fallback reasons so tuning is visible in DNS logs.
4. Update `doc/architecture/DNS_TRANSPORT.md` to describe the new mode,
   including its header-only response guarantee, trade-offs, and the
   conditions that activate it.

## Validation

- If tests are requested later, run DNS clamp-related unit tests with
  `python3`; do not run tests under `tests/e2e/`.
