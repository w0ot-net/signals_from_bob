# Bob Retransmit Poll Hint Only Plan

## Goal
- When a retransmit exceeds the per-request response cap, Bob responds with a
  KEEPALIVE + POLL_HINT packet and no segments.
- Avoid emitting synthetic control segments just to carry the poll hint.

## Non-Goals
- Change retransmit selection, cooldown rules, or poll pacing.
- Change Alice clamp logic or DNS transport behavior.
- Update tests under ./tests (handled later).

## Affected Components
- sfb/tunnel/bob_tunnel.py
- doc/BOB_RETRANSMIT_LOGIC.md

## Plan
1. Replace the cap-blocked retransmit response with a poll-hint keepalive.
   - In `_send_retransmit_response`, keep the existing send-window full drop
     logic, then send `_send_keepalive_response(..., poll_hint=True)` instead
     of `_send_poll_hint_segment(...)`.
   - Update the warning message and/or fields for
     `tunnel.retransmit_cap_blocked` so it is clear the response is a
     KEEPALIVE + POLL_HINT with no segments.

2. Stop injecting a synthetic control message for poll-hint responses.
   - Remove `self.control.send_message(tun_ping())` from
     `_send_poll_hint_segment`.
   - If there are no queued control segments, fall back to
     `_send_keepalive_response(..., poll_hint=True)`.
   - If queued control segments exist, continue sending them with
     FLAG_POLL_HINT set.

3. Document the cap-blocked behavior in Bob retransmit notes.
   - Add a short note to `doc/BOB_RETRANSMIT_LOGIC.md` that when a retransmit
     exceeds the per-request response cap, Bob sends KEEPALIVE + POLL_HINT with
     no segments, matching `doc/TUNNEL.md` and `doc/ASYMMETRY.md`.

## Validation
- Run existing non-e2e unit tests with `python3` if desired; do not run tests
  under `tests/e2e/`.

## Execution Notes (20260106)
- Replaced cap-blocked retransmit responses with KEEPALIVE + POLL_HINT.
- Removed synthetic tun_ping injection for poll-hint responses.
- Documented the cap-blocked behavior in Bob retransmit notes.
- Validation not run (tests not executed).
