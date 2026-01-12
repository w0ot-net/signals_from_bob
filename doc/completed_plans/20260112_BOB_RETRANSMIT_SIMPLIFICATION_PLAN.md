# Bob Retransmit Simplification Plan

Status: completed

## Summary
Replace Bob's retransmit decision flow with a minimal, poll-driven sequence
that uses a single cooldown gate and window enforcement. Remove ACK-silence
and ACK-progress gates from Bob to reduce complexity and align with protocol
asymmetry.

## Goals
- Replace Bob's retransmit decision logic with a single, easy-to-reason flow.
- Keep Bob opportunity-driven: one response per poll, cooldown based on poll
  EWMA, and no RTT tracking.
- Preserve window safety (full/distance) and keepalive suppression when
  pending data exists.
- Simplify logging and documentation to match the new flow.

## Non-Goals
- Change Alice retransmit behavior or RTT logic.
- Alter cooldown configuration defaults or poll EWMA updates.
- Add or run automated tests.
- Modify transport-specific behavior or response caps.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Replace `_select_response_action` with a new minimal flow.
   - Compute `oldest_info` once and derive `cooldown` plus `oldest_age`.
   - Determine `retransmit_due` using only `oldest_age >= cooldown`.
   - Keep window enforcement checks, but fold them into the same decision path
     so the override behavior is explicit and compact.

2. Remove ACK-silence gating from Bob.
   - Stop using `ack_silence()` or `ack_progress_silence()` in Bob's
     retransmit path.
   - Remove related skip reasons and fields from `tunnel.retransmit_skip`.

3. Preserve keepalive suppression with pending data.
   - Keep the existing pending-data signal from segment collection.
   - Ensure the keepalive action is only selected when there are no segments
     and no pending data.

4. Simplify retransmit logging.
   - Keep `tunnel.retransmit_skip` only for the cooldown gate, with fields for
     `age`, `cooldown`, `poll_ewma`, and `unacked`.
   - Remove cumulative-ack fields from the skip log.

5. Update protocol documentation.
   - Update `doc/architecture/BOB_RETRANSMIT_LOGIC.md` to describe the new
     flow and the single cooldown gate.
   - Update `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md` to remove ACK
     silence gating details.
   - Add a brief note to `doc/architecture/ASYMMETRY.md` reflecting the new
     simplified, cooldown-only retransmit gate on Bob.

## Testing
- Do not run tests.

## Execution Notes
- Simplified Bob retransmit selection to a cooldown-only gate with window
  overrides and streamlined skip logging.
- Preserved keepalive suppression by honoring pending-data signals when
  selecting responses.
- Updated Bob retransmit and asymmetry docs to match the new cooldown-only
  gating description.
