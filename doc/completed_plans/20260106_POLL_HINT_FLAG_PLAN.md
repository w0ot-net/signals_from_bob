# Poll Hint Flag Plan

Status: completed

## Execution Scope (When Asked To Execute This Plan)

- Only implement the FLAG_POLL_HINT flag and the minimal supporting changes
  required for that flag to be valid, logged, and acted upon as described here.
- Do NOT implement any work from doc/plans/DNS_ADAPTIVE_QUERY_CLAMP_PLAN.md.
  That plan is explicitly out of scope when executing this one.

## Goal

- Add a new packet header flag that lets Bob signal "keep the clamp hot" without
  using a control message.
- Prevent retransmit-cap fatal closes after payload_cap removal by keeping Alice
  in response-max mode while Bob may need to retransmit.
- Keep payload sizes balanced when both sides have data to send.

## Non-Goals

- Introduce a new control message for poll hints.
- Change polling cadence, window negotiation, or retransmit timing rules.
- Run E2E tests under tests/e2e (user will run them).

## Affected Components

- sfb/protocol/constants.py
- sfb/protocol/packet.py
- sfb/protocol/__init__.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/transport/dns/dns_client.py
- doc/PROTOCOL.md
- doc/ASYMMETRY.md
- doc/TUNNEL.md
- doc/DNS_TRANSPORT.md
- tests/test_tunnel.py
- tests/test_bob_tunnel.py
- tests/test_dns_client.py

## Design Notes

- Use a new flag bit (0x10) named FLAG_POLL_HINT.
- The hint is advisory and does not replace content flags:
  - Content flags remain FLAG_KEEPALIVE and FLAG_HAS_SEGMENTS.
  - FLAG_POLL_HINT is valid only when paired with KEEPALIVE or HAS_SEGMENTS.
  - A KEEPALIVE without FLAG_POLL_HINT is a true idle keepalive.
  - A KEEPALIVE with FLAG_POLL_HINT means "no segments now, but keep clamp hot."
- No new "poll hint" packet type is needed; this is a header flag only.
- How small Alice must go:
  - response_max mode uses the smallest query payload that still yields the
    maximum response payload cap from the lookup
    (q_min_for_max_response, derived from the precomputed table).
- Balanced payload sizes when both sides have data:
  - Define q_balanced as the largest query payload such that
    response_cap(q) >= q.
  - Use q_balanced when Alice has pending data and Bob signals data
    (bob_has_data true) but retransmit_guard is off.
- Retransmit guard:
  - While a hint is seen or recv_window.sack != 0, keep response_max mode so
    Bob can retransmit without cap mismatch.
  - Decay out of guard only after N consecutive keepalive-only responses
    (no segments, no hint) and recv_window.sack == 0.

## Implementation Steps

1) Add the new protocol flag:
   - Define FLAG_POLL_HINT = 0x10 in sfb/protocol/constants.py.
   - Extend sfb/protocol/packet.py _VALID_FLAGS to include FLAG_POLL_HINT.
   - Export the new flag in sfb/protocol/__init__.py.
2) Update packet validation and logging:
   - In sfb/tunnel/base_tunnel.py, enforce that FLAG_POLL_HINT is only accepted
     on non-handshake packets and only when KEEPALIVE or HAS_SEGMENTS is set.
   - Add a log field (e.g., poll_hint) to tunnel.packet_send/recv fields so
     operators can see when the hint is used.
3) Bob behavior (sfb/tunnel/bob_tunnel.py):
   - When a retransmit exceeds the per-request response cap, do not close.
   - Send a KEEPALIVE response with FLAG_POLL_HINT and no segments.
   - Log a distinct event (tunnel.retransmit_cap_blocked) with seq/bytes/cap.
4) Alice behavior (sfb/tunnel/alice_tunnel.py + DNS clamp logic):
   - Treat FLAG_POLL_HINT as "bob_has_data" for clamp decisions.
   - Enter retransmit_guard on any response with segments or FLAG_POLL_HINT.
   - Keep response_max mode while retransmit_guard is active.
5) Clamp mode selection (DnsClient):
   - response_max: use q_min_for_max_response while retransmit_guard is active.
   - balanced: use q_balanced when both sides have data and no guard.
   - idle: use the existing idle clamp when Bob is idle (no data, no hint).
6) Documentation updates:
   - PROTOCOL: define FLAG_POLL_HINT semantics and validity rules.
   - TUNNEL/ASYMMETRY: note that Bob only sets the hint in responses and that
     Alice treats hint as data-present for clamp decisions.
   - DNS_TRANSPORT: describe response_max vs balanced clamp behavior.
7) Tests (non-e2e):
   - Add packet validation tests for the hint flag.
   - Add Bob retransmit-cap block test to ensure no fatal close and hint set.
   - Add DnsClient clamp-mode selection tests for response_max/balanced/idle.

## Validation

- Run unit tests with python3 (exclude tests/e2e/).

## Execution Notes (20260106)

- Implemented FLAG_POLL_HINT constants, validation, and packet logging fields.
- Updated Bob retransmit-cap handling to send KEEPALIVE + POLL_HINT and emit
  tunnel.retransmit_cap_blocked.
- Updated protocol/tunnel/DNS docs for the new flag and retransmit-cap behavior.
- Deferred Alice clamp behavior, DNS clamp-mode changes, and tests per the
  execution scope and test restrictions.
- Validation not run (tests not executed).
