# Pacer Gating Controller Plan

Status: draft

## Goal
- Move pacer gating and feedback-freeze logic out of `AliceTunnel` into a
  reliability-side controller that returns "can send" decisions, block reasons,
  and feedback-freeze actions.
- Keep pacing behavior, thresholds, and log fields consistent with the current
  implementation.

## Non-Goals
- Change AdaptivePacer algorithms, SendWindow distance rules, or thresholds.
- Alter log event names or schemas beyond what the controller returns.
- Run tests here.

## Affected Components
- sfb/tunnel/alice_tunnel.py
- sfb/reliability/pacing.py
- sfb/reliability/send_window.py
- sfb/reliability/__init__.py
- sfb/reliability/pacer_gate.py (new)

## Plan
1. Map the current gating flow in `sfb/tunnel/alice_tunnel.py`.
   - Document inputs/outputs for `_check_send_window_distance`,
     `_check_send_pacer`, `_should_freeze_pacer_feedback`,
     `_update_pacer_feedback_freeze`, and `_maybe_unfreeze_pacer_feedback`.
   - Capture the block reasons (`window_distance`, `pacer`) and freeze reasons
     (`sack_stall`, `stall_clear`, `distance_clear`) along with the fields used
     for logging.
2. Introduce a reliability-side controller in
   `sfb/reliability/pacer_gate.py` (e.g., `PacerGateController`).
   - Provide a method that accepts `send_window`, `pacer`, timing inputs
     (`now`, `srtt_ms`, `rto_sec`), the fast-retransmit age ratio,
     `keepalive_only`, `pacer_cap`, and `max_window`.
   - Return a decision dict with `can_send`, `block_reason`, `block_details`,
     and `pacer_cap`, plus optional `freeze_action`/`freeze_reason` and
     `freeze_details` for logging.
   - Apply the feedback-freeze decisions inside the controller by calling
     `pacer.freeze_feedback`/`pacer.unfreeze_feedback`, but surface the
     resulting action so the tunnel can log it.
3. Update `sfb/tunnel/alice_tunnel.py` to use the controller.
   - Replace `_check_send_window_distance` and `_check_send_pacer` with the
     controller decision results.
   - Remove `_should_freeze_pacer_feedback`, `_update_pacer_feedback_freeze`,
     and `_maybe_unfreeze_pacer_feedback` once the controller handles those
     actions.
   - Keep logging functions in `AliceTunnel`, driven by the controller output,
     so log structure and messages remain unchanged.
4. Export the controller from `sfb/reliability/__init__.py` and update imports
   in `AliceTunnel`.
5. Confirm behavior parity by comparing decision outcomes and logging fields
   in the old vs. new paths.

## Testing
- Do not run tests here. The user can run python3 tests if needed.
