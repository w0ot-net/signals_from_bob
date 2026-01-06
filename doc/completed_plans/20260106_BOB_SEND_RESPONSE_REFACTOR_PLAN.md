# Bob Send Response Refactor Plan

## Goal
- Separate decision logic from send/log side effects in
  sfb/tunnel/bob_tunnel.py:_send_response to flatten branching and reduce
  repeated logging.
- Preserve behavior for retransmit gating, window/distance blocking, payload-cap
  clamping, keepalive suppression, and counters.
- Make send outcomes explicit and easier to reason about.
- eliminate duplicated or unnecessary code

## Non-Goals
- Change retransmit, windowing, or keepalive behavior.
- Modify transport or responder interfaces.
- Update or run tests.

## Affected Components
- sfb/tunnel/bob_tunnel.py

## Plan
1) Introduce a small response decision structure (namedtuple or dict) that
   captures the chosen action (retransmit, window_blocked, distance_blocked,
   poll_hint, keepalive, segments) plus any needed fields (context, reason,
   seq/segments, encrypted_body, response_data).
2) Extract decision logic into a helper like _select_response_action(now,
   response_payload_cap) that:
   - Evaluates opportunistic retransmit and skip reasons.
   - Applies send window and distance gating, deciding on fallback actions.
   - Computes max payload with response payload-cap clamping and collects
     segments/pending state.
   - Preserves keepalive suppression when pending data exists.
3) Add dedicated send helpers for the non-retransmit paths (for example,
   _send_keepalive_response and _send_segments_response) to own encoding,
   send_window updates, counters, responder calls, and packet_send logging.
   Reuse existing _send_retransmit_response and _send_poll_hint_response.
4) Update _send_response to:
   - Read responder payload-cap, call the selector, and dispatch based on the
     decision action.
   - Centralize send_blocked/keepalive_suppressed/reliability_state logging
     using fields from the decision structure.
5) Review the diff to confirm Python 2.7/3 compatibility and that observable
   behavior and log fields are unchanged.

## Execution Notes
- Added response selection and send helpers to separate decision logic from
  send/log side effects.
- Centralized send-blocked and keepalive-suppressed logging in the dispatch
  path while preserving existing fields.
