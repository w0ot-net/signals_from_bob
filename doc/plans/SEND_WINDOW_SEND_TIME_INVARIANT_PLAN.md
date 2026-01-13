# Send Window Send-Time Invariant Plan

Status: draft

## Summary
Enforce a non-None `send_time` invariant strictly at write time in
`SendWindow` (initial send and retransmit) and remove fallback behavior that
masks missing times. Keep the change minimal by avoiding new read-path checks
and only handling violations at the write boundary, with fatal logging in the
tunnel loops.

## Goals
- Guarantee `send_time` (and `first_send_time`) is set when unacked packets are
  created or updated.
- Remove fallback behavior that treats missing `send_time` as `0.0`.
- Surface invariant violations as fatal errors with clear logging and close.
- Align Bob retransmit documentation with the invariant.

## Non-Goals
- Change Alice or Bob retransmit policy beyond the write-time invariant.
- Add new transport behavior or MTU negotiation changes.
- Add or run automated tests; do not touch `tests/` or `tests/e2e/`.
- Add broad read-path validation checks beyond the write boundary.

## Affected Components
- `sfb/reliability/send_window.py`
- `sfb/reliability/fast_retransmit.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md`

## Plan
1. Enforce `send_time` on write in `SendWindow`.
   - Define `SendWindowError`.
   - In `send()` and `mark_retransmit()`, resolve `now` and raise
     `SendWindowError` if it is None before storing.
   - Leave read paths untouched beyond removing fallback behavior.

2. Remove fallback behavior that hides missing times.
   - Drop `send_time`/`first_send_time` fallbacks to `0.0` in oldest selection
     and heap rebuilds, so missing values are not silently masked.
   - Remove `send_time is None` compatibility branches in fast retransmit and
     Bob retransmit logging so the invariant is assumed.

3. Treat invariant violations as fatal at the tunnel layer.
   - Catch `SendWindowError` in `AliceTunnel.tick()` and
     `BobTunnel.handle_request()`.
   - Log `tunnel.send_window_inconsistent` with `seq`, `context`, and `side`,
     then close and re-raise to stop the loop.

4. Update documentation to match the invariant.
   - Replace "treated as 0.0" and "gate skipped" language in Bob retransmit
     docs with a fatal invariant statement.

## Testing
- Do not run tests unless requested; use `python3` if needed later.
