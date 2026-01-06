# Bob Tunnel Dedup Plan

Status: draft

## Goal

Reduce duplicated control flow in `sfb/tunnel/bob_tunnel.py` without changing
behavior, logging, or performance.

## Non-goals

- No protocol or state machine changes.
- No changes to logging content or order.
- No new tests; e2e tests remain user-run only.

## Affected Components

- `sfb/tunnel/bob_tunnel.py` (serve loops, request routing, response helpers)

## Plan

1. Add a small private helper to centralize the recv/dispatch loop logic used by
   `serve_forever` and `_run_loop`, preserving the existing log messages and
   idle-timeout behavior differences.
2. Collapse the duplicated handshake dispatch in `handle_request` by routing
   both `DISCONNECTED` and `CONNECTING` states through a single branch.
3. Introduce a private send helper to encapsulate the shared send-window record,
   metrics updates, responder call, and packet_send logging for
   `_send_poll_hint_response`, `_send_keepalive_response`, and
   `_send_segments_response`.
4. Reuse the same send helper for the shared tail in `_send_retransmit_response`,
   keeping the existing retransmit-specific logging intact.
5. Review the updated code to confirm no ordering changes for metrics or logging
   and no extra work in hot paths.

## Validation

- Code review focused on logging parity, send_window updates, and responder
  error handling.
- Do not run `tests/e2e`; any testing is left to the user.
