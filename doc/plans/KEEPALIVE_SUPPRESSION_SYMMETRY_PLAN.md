# Keepalive Suppression Symmetry Plan

Status: draft

## Summary
Make keepalive suppression consistent between Alice and Bob by centralizing the
decision in BaseTunnel and using segment collection to surface pending data
even when no segments fit. Preserve the poll/response asymmetry.

## Goals
- Share a single keepalive decision helper for both Alice and Bob.
- Suppress keepalive-only packets when any channel has pending data.
- Preserve existing retransmit gating and MTU negotiation behavior.

## Non-Goals
- Change retransmit rules, poll pacing, or timeout policies.
- Modify transport-specific response caps or DNS behavior.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Add a shared keepalive decision helper in `sfb/tunnel/base_tunnel.py`.
   - Implement `_should_send_keepalive(pending_data, keepalive_due)` returning
     True only when keepalive is due and no channel has pending data.
   - Keep the helper minimal and avoid comprehensions for PY2 safety.

2. Surface pending-data state from segment collection on both sides.
   - Use `_collect_segments(..., return_pending=True)` to return
     `(segments, pending_data)` in the Alice send path and in Bob response
     selection.
   - When `segments` is empty but `pending_data` is True, treat this as a
     keepalive-suppressed case rather than a keepalive-only send.

3. Apply the helper to Alice polling decisions.
   - Thread `pending_data` through `_try_send_segments` or the poll loop so the
     keepalive-only path checks `_should_send_keepalive`.
   - Ensure keepalive suppression still respects window and pacing gates.

4. Apply the helper to Bob response selection.
   - In `_select_response_action`, decide `keepalive` only when
     `_should_send_keepalive(pending_data, keepalive_due=True)` is True.
   - Add a targeted log event when keepalive is suppressed due to pending data
     but no segments fit, to quantify how often it happens.

5. Update documentation.
   - Note the shared keepalive suppression rule in
     `doc/architecture/ASYMMETRY.md` and clarify the pending-data edge case.

## Testing
- Do not run tests.
