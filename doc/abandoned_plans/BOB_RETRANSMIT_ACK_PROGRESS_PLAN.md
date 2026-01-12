# Bob Retransmit Ack Progress Plan

Status: abandoned

## Summary
Switch Bob's opportunistic retransmit gate to use ACK progress silence
(time since any packet was acked) instead of cumulative ACK silence. This
prevents spurious retransmits when SACK advances without moving the cumulative
ACK, while preserving the existing cooldown and window rules.

## Goals
- Use ACK progress silence for Bob's retransmit cooldown gate.
- Keep cooldown computation, poll EWMA behavior, and window overrides intact.
- Update logging and docs to reflect the ACK progress gate accurately.

## Non-Goals
- Change cooldown configuration or poll EWMA parameters.
- Modify Alice retransmit behavior.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Swap the ACK silence gate to use ACK progress silence.
   - In `_select_response_action`, replace `ack_silence()` with
     `ack_progress_silence()` and update variable names.
   - Keep the gate semantics: skip when `age < cooldown` or
     `ack_progress_silence < cooldown`.

2. Update retransmit-skip logging fields.
   - Rename `since_cum_ack` to `since_ack_progress` and include
     `last_ack_progress_time` where useful.
   - Retain the existing `cooldown`, `age`, and poll EWMA fields.

3. Align documentation with the ACK progress gate.
   - Update `doc/architecture/BOB_RETRANSMIT_LOGIC.md` and
     `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md` to describe ACK progress
     silence rather than cumulative ACK silence.
   - Add a short note in `doc/architecture/ASYMMETRY.md` clarifying that Bob's
     cooldown gate tracks any ACK progress, including SACK.

## Testing
- Do not run tests.

## Abandon Notes
- Abandoned per request; revisit if ACK progress gating becomes a priority.
