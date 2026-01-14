# Window Negotiation Invariants Plan

Status: draft

## Summary
Enforce strict window-size invariants during negotiation: missing, invalid, or
out-of-range sizes are protocol violations (log + close) rather than defaulting
or clamping.

## Goals
- Fail fast on invalid `window` and `window_ok` sizes.
- Align BaseTunnel behavior with invariant-first guidance.
- Preserve existing negotiation flow for valid sizes.

## Non-Goals
- Change window growth policy or pacing.
- Modify MTU negotiation or other control messages.
- Add or run tests.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `doc/architecture/CONTROL_MESSAGES.md`

## Plan
1. Enforce required size fields in `_handle_window`.
   - Stop defaulting to `self._default_window` when `size` is missing.
   - Require `size` to be an integer >= 1.
   - Compute `max_allowed = min(self._proposed_window, self.MAX_WINDOW)` and
     treat `size > max_allowed` as a protocol violation (log + close).
   - Keep the negotiation response path unchanged for valid sizes.

2. Enforce required size fields in `_handle_window_ok`.
   - Require `size` to be an integer >= 1.
   - Use the same `max_allowed` calculation and treat `size > max_allowed` as
     a protocol violation (log + close).
   - Remove the clamp-and-continue fallback; only apply valid sizes.

3. Keep violation logging precise and minimal.
   - Use `_close_protocol_violation` with distinct reasons (missing, invalid,
     too_large).
   - If needed, emit a focused log event with `size` and `max_allowed` before
     closing without adding extra complexity.

4. Update protocol documentation.
   - In `doc/architecture/CONTROL_MESSAGES.md`, document that `window` and
     `window_ok` require `size` and that missing/invalid/out-of-range values are
     fatal protocol violations (log + close).

## Testing
- Do not run tests.
