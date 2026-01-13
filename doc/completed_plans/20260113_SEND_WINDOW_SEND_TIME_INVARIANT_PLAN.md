# Send Window Send-Time Invariant Plan

Status: completed

## Summary
Enforce a non-None `send_time` invariant in `SendWindow` and treat missing
`send_time` as a fatal send-window inconsistency. Centralize detection in the
reliability layer, propagate errors to tunnels for clean shutdown, and update
Bob retransmit docs/plans to remove the "treated as 0.0" behavior.

## Goals
- Ensure every unacked packet has a valid `send_time` on insert and retransmit.
- Remove fallback behavior that treats missing `send_time` as `0.0`.
- Propagate invariant violations as fatal errors with clear logging and close.
- Align docs and existing plans with the invariant.

## Non-Goals
- Change Alice or Bob retransmit policy beyond the invariant.
- Add new transport behavior or MTU negotiation changes.
- Add or run automated tests; do not touch `tests/` or `tests/e2e/`.

## Affected Components
- `sfb/reliability/send_window.py`
- `sfb/reliability/fast_retransmit.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/plans/BOB_RETRANSMIT_SIMPLIFICATION_PLAN.md`

## Plan
1. Add an explicit send-window invariant and error type.
   - Define `SendWindowError` in `sfb/reliability/send_window.py`.
   - Add a helper to validate `send_time` and raise with context/seq.
   - In `send()` and `mark_retransmit()`, resolve `now` and validate it is not
     `None` before storing to `_unacked`.

2. Remove missing-`send_time` fallbacks in `SendWindow`.
   - Remove `send_time` fallback handling in oldest selection and heap rebuilds.
   - Validate `send_time` once per operation before retransmit scans, oldest
     selection, and ACK processing.

3. Treat invariant failures as fatal at the tunnel layer.
   - Catch `SendWindowError` in `AliceTunnel.tick()` and
     `BobTunnel.handle_request()` (or the top-level send/receive loop).
   - Log `tunnel.send_window_inconsistent` with `seq`, `context`, and `side`,
     close the tunnel, then re-raise to stop the loop.

4. Remove `send_time is None` compatibility branches in call sites.
   - `sfb/reliability/fast_retransmit.py`: drop the `send_time is None` early
     return and compute age directly.
   - `sfb/tunnel/bob_tunnel.py` and `sfb/reliability/send_window.py` debug
     paths: remove conditional age calculations that assume `send_time` can be
     `None`.

5. Update docs and plans to reflect the invariant.
   - `doc/architecture/BOB_RETRANSMIT_LOGIC.md`: replace the "treated as 0.0"
     note with a fatal invariant statement.
   - `doc/plans/BOB_RETRANSMIT_SIMPLIFICATION_PLAN.md`: replace the `None`
     handling with the invariant language.

## Testing
- Do not run tests unless requested; use `python3` if needed later.

## Execution Notes
- 2026-01-13: Enforced non-None `send_time` in `SendWindow`, raised
  `SendWindowError` on invariant violations with centralized validation,
  updated Alice/Bob tunnel handling, and aligned retransmit docs with the
  invariant.
