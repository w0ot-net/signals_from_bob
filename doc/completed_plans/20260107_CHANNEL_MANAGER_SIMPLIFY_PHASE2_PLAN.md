# Channel Manager Simplify Phase 2 Plan

## Goal
- Reduce complexity in control dispatch and segment collection.
- Keep packing order and channel scheduling behavior identical.

## Non-Goals
- Change MTU math, buffering limits, or backoff behavior.
- Rework channel state transitions.
- Run tests here.

## Affected Components
- sfb/channel/channel_manager.py

## Plan
1. Refactor control message dispatch:
   - Replace the if/elif chain in `handle_control_message()` with a dict
     mapping `cmd` to handler methods; ignore missing keys as today.
2. Factor segment draining helpers:
   - Add `_take_segment(channel_id, channel, remaining, segments)` to encapsulate
     `_take_send_data` and remaining-byte updates.
   - Add `_drain_channels(channel_ids, snapshot, remaining, segments)` to reuse
     for primary and round-robin passes while preserving order.
3. Streamline active-channel snapshot cleanup:
   - Add a helper that returns `(active_ids, channel_snapshot, inactive_ids)`
     to remove the double loop and reduce bookkeeping.
   - Keep the `inactive_ids` removal under the existing lock.

## Testing
- Do not run tests here. The user will run tests with python3 if needed.

## Notes
- Preserve the `CHANNEL_MANAGER.md` packing rules exactly; this is refactoring
  only.

## Execution Notes
- Added control handler dispatch table plus snapshot/drain helpers and rewired
  segment collection to reuse them without changing packing order.
- Tests not run (not requested).
