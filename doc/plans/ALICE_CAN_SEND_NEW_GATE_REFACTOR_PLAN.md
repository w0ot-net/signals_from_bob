# Alice Can-Send Gate Refactor Plan

## Goal
- Simplify sfb/tunnel/alice_tunnel.py:_can_send_new by extracting gate checks
  that return a reason/context payload for centralized logging.
- Preserve current gate order, keepalive_only behavior, allow_window_full
  behavior, pacing/rate-limit rules, and logging fields.
- Reduce repeated logging and reliability-state emission.

## Non-Goals
- Change pacing, rate limiting, window/distance thresholds, or retransmit rules.
- Add new log events or alter existing field payloads.
- Update or run tests.

## Affected Components
- sfb/tunnel/alice_tunnel.py

## Plan
1) Define a small gate decision structure (dict or namedtuple) with fields like
   action ('allow'|'block'), reason, context, and extra_fields; include a flag
   to suppress logging for silent gates (serial window negotiation).
2) Extract gate helpers that return either None (allowed) or a decision:
   - _check_serial_window_block(now) for serial window negotiation unacked gate.
   - _check_send_window_full(allow_window_full, keepalive_only).
   - _check_send_window_distance(now, pacer_cap, keepalive_only).
   - _check_send_rate_limit(keepalive_only).
   - _check_send_pacer(now, keepalive_only, cap) (and keep _log_pacer_state
     behavior intact).
3) Add a centralized _log_send_blocked(decision, now) helper to emit:
   - send_blocked log_event with the right message/fields.
   - tunnel.send_window_distance and distance_details when applicable.
   - _log_reliability_state with keepalive_only and any distance metadata.
   - _note_pacer_blocked for window_full and window_distance.
4) Update _can_send_new to compute pacer_cap once (when enabled), call helpers
   in the existing order, and funnel all blocking through _log_send_blocked.
5) Review the diff to confirm Python 2.7/3 compatibility, preserved log fields,
   and unchanged gating outcomes for each reason.
