# Channel Manager Simplify Phase 1 Plan

## Goal
- Reduce duplication in channel creation and logging helpers.
- Keep behavior, performance, and log content unchanged.

## Non-Goals
- Change channel lifecycle behavior or control protocol.
- Modify packing rules or scheduling logic.
- Run tests here.

## Affected Components
- sfb/channel/channel_manager.py

## Plan
1. Add a small channel factory helper:
   - `_new_channel(channel_id, state)` builds a `Channel` with config defaults
     and sets the initial state.
   - Use it in `open_channel()` and `_handle_open()` to remove repeated setup.
2. Centralize log context:
   - Cache `self._side` in `__init__` (`'alice'` or `'bob'`).
   - Add `_log_ctx(ch=None, **extra)` to return the shared log dict.
   - Replace repeated `{'ch': channel_id, 'side': ...}` blocks with helper calls.
3. Consolidate control-channel guard:
   - Add `_reject_control_channel(cmd, channel_id, message)` returning True when
     the control channel should be ignored.
   - Use it in `_handle_close()`, `_handle_close_err()`, `_handle_close_ok()`.

## Testing
- Do not run tests here. The user will run tests with python3 if needed.

## Notes
- Keep helper bodies small to avoid hiding control flow.
