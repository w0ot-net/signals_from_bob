# Bob Retransmit Simplification Plan

Status: draft

## Summary
Replace Bob's retransmit decision flow with a minimal poll-driven sequence that
uses a single cooldown gate and explicit window enforcement overrides. Remove
ACK-silence gating from Bob while preserving keepalive suppression, window
safety, and existing cooldown configuration.

## Goals
- Replace Bob's retransmit decision logic with a single cooldown gate:
  retransmit when `oldest_age >= cooldown`.
- Keep Bob opportunity-driven: one response per poll, no RTT tracking, cooldown
  derived from poll EWMA and existing config.
- Preserve window safety: window-full and window-distance checks still override
  the cooldown gate and retransmit the oldest unacked packet.
- Preserve keepalive behavior: keepalive only when no segments fit; use
  `POLL_HINT` when pending data remains.
- Simplify retransmit skip logging to a single cooldown reason with minimal
  fields.

## Non-Goals
- Change Alice retransmit behavior, RTT logic, or send-window tracking.
- Alter cooldown configuration defaults or poll EWMA updates.
- Change transport-specific response caps or MTU negotiation.
- Add or run automated tests; do not touch `tests/` or `tests/e2e/`.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/ASYMMETRY.md`
- `doc/architecture/PROTOCOL.md`
- `doc/architecture/TUNNEL.md`

## Plan
1. Replace `_select_response_action` in `sfb/tunnel/bob_tunnel.py` with a
   cooldown-only gate.
   - Compute `oldest_info = self._send_window.get_oldest_unacked_info()` once
     per poll.
   - If `oldest_info` exists, unpack `(seq, segments, flags, encrypted_body,
     send_time, retransmit_count)` and compute:
     - `cooldown = self._retransmit_cooldown()` (unchanged).
     - `oldest_age = None` if `send_time` is `None`, else `now - send_time`.
     - `retransmit_due = (oldest_age is not None and oldest_age >= cooldown)`.
   - If `retransmit_due` is True, return decision
     `{'action': 'retransmit', 'context': 'retransmit', ...}` using the oldest
     packet.
   - If `retransmit_due` is False and `oldest_info` exists, emit
     `tunnel.retransmit_skip` for reason `cooldown` (see logging step) and
     continue to window enforcement.
   - Keep response payload cap handling and `_collect_segments` usage
     unchanged.
   - Use explicit assignments only (no comprehensions) inside `sfb/`.

2. Preserve window enforcement as explicit overrides after the cooldown gate.
   - If `not self._send_window.can_send`, return `window_blocked` with:
     - `context='window_full'`, `reason='window_full'`, `oldest_info`,
       `unacked`, `max_in_flight`.
   - If `distance_exceeded`, return `distance_blocked` with:
     - `context='window_distance'`, `reason='window_distance'`, `oldest_info`,
       `distance_info`.
   - Keep the override behavior: `_send_response` should still retransmit
     `oldest_info` on these actions even when the cooldown gate did not pass.

3. Preserve keepalive suppression semantics with pending data.
   - After window checks, call `_collect_segments(..., return_pending=True)`
     and decide:
     - `segments` non-empty: action `segments`, `poll_hint` set to
       `bool(pending_data)`.
     - `segments` empty and `pending_data` True: action `keepalive` with
       `poll_hint=True` (cap-limited pending data case).
     - `segments` empty and `pending_data` False: action `keepalive` with
       `poll_hint=False` (true idle keepalive).
   - This keeps idle keepalive pongs suppressed when data is queued, except
     for the cap-limited poll-hint case.

4. Simplify retransmit skip logging in `sfb/tunnel/bob_tunnel.py`.
   - Keep `tunnel.retransmit_skip` only for the cooldown gate in
     `_select_response_action`.
   - Emit fields:
     - `seq`, `reason='cooldown'`, `age` (rounded), `cooldown`, `poll_ewma`,
       `unacked`, `side`.
   - Remove cumulative ACK fields (`since_cum_ack`, `last_cum_ack`) and the
     `ack_progress` reason.
   - Remove the `tunnel.retransmit_skip` log emitted for the response cap path
     in `_send_retransmit_response`; keep `tunnel.retransmit_cap_blocked` as
     the only cap log.

5. Update documentation to match the simplified gate.
   - `doc/architecture/BOB_RETRANSMIT_LOGIC.md`:
     - Replace the ACK-progress gate description with a single cooldown gate.
     - Update skip conditions, reasons, and `tunnel.retransmit_skip` fields.
     - Remove `ack_progress` and `cap` skip reason mentions if those logs are
       removed.
     - Clarify window override behavior as cooldown-independent retransmit
       triggers.
   - `doc/architecture/ASYMMETRY.md`: remove the ACK-progress mention in Bob's
     retransmit description.
   - `doc/architecture/PROTOCOL.md`: remove "skips after recent ack progress"
     from the Bob bullet.
   - `doc/architecture/TUNNEL.md`: update the Bob section to reference
     cooldown-only gating.

## Testing
- Do not run tests (user-only for `tests/e2e/`); if any manual checks are
  requested later, use `python3`.
