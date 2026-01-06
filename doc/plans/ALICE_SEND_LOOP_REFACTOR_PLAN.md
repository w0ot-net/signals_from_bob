# Alice Send Loop Refactor Plan

## Goal
- Reduce duplication in the Alice send loop by consolidating serial-window and
  normal-path send logic.
- Make the pending-data predicate and control-only selection explicit so pacing
  and keepalive behavior cannot drift between branches.

## Non-Goals
- Change send pacing, keepalive timing, retransmit rules, or window
  negotiation behavior.
- Alter _has_pending_data_acks semantics or idle-sleep behavior.
- Modify Bob-side logic or transport implementations.
- Update or run tests.

## Affected Components
- sfb/tunnel/alice_tunnel.py

## Plan
1) Introduce a small helper (private method or inner function) that returns the
   pending predicate and control_only flag based on serial_window, so the same
   predicate is used for both the "send pending data" path and keepalive
   suppression.
2) Extract the duplicated "try send pending segments" block into a helper that
   accepts pending predicate, control_only, and a "break_on_empty" policy (serial
   window keeps the loop alive; normal path breaks when pending but no segments).
3) Replace the serial_window/normal branching in the send loop with the helper,
   preserving _can_send_new checks, pacing checks, _has_pending_data_acks
   updates, and the existing break/continue behavior.
4) Reuse the same pending predicate for the keepalive suppression checks to
   ensure pongs stay suppressed when any channel reports pending data.
5) Review the diff to confirm no behavior changes beyond the refactor and that
   the loop remains Python 2.7/3 compatible.
