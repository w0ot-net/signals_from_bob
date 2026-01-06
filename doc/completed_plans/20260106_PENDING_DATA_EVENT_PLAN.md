# Pending Data Event Plan

## Goal
- Provide a correct, low-overhead pending-data signal for the hot send loop.
- Avoid combined-event clears when control data drains but data channels remain
  pending.
- Ensure active-channel pruning keeps the pending-data signal accurate.

## Non-Goals
- Change channel packing order or segment sizing rules.
- Alter keepalive suppression semantics.
- Modify transport behavior or protocol flags.
- Add or run E2E tests.

## Affected Components
- sfb/channel/channel_manager.py
- sfb/channel/control_channel.py
- sfb/tunnel/alice_tunnel.py
- doc/CHANNEL_MANAGER.md

## Plan
1) Add an explicit data-pending event in ChannelManager.
   - Create `self._data_send_event = threading.Event()` to reflect pending
     non-control channel data only.
   - Add helpers (locked) to set/clear the event when `_active_channels`
     transitions between empty and non-empty.
   - Ensure `_register_channel_locked`, `_on_channel_send_state`, and
     `_unregister_channel_locked` update the event alongside `_active_channels`.

2) Ensure collect_segments pruning updates pending state.
   - After pruning `inactive_ids` in `collect_segments`, re-evaluate whether
     `_active_channels` is empty and clear the data event if needed.
   - Avoid leaving the data event stuck set when the active list is emptied by
     pruning.

3) Keep control and data events separate to avoid incorrect clears.
   - Continue using the control channel's own `send_event` for control data.
   - Do not reuse the control event as a combined signal.
   - Update `has_pending_data()` to use `control.send_event.is_set()` OR
     `data_send_event.is_set()` without taking the active-channel lock.

4) Adjust Alice hot-loop checks if needed.
   - Confirm Alice's `has_pending_data(mode=...)` calls still express the
     correct intent (control-only vs data-only vs combined) with the new event.
   - Keep keepalive suppression logic unchanged; only the pending predicate
     becomes lock-free.

5) Document pending event semantics.
   - Update `doc/CHANNEL_MANAGER.md` to describe the control vs data pending
     events and how `has_pending_data()` derives combined state.

## Testing
- Do not run tests here. The user will run E2E tests as needed.

## Execution Notes
- Added a ChannelManager data-pending event and switched pending checks to
  use it alongside the control `send_event`.
- Updated active-channel mutation paths (send-state updates and pruning) to
  keep the data-pending event accurate.
- Documented pending event semantics in `doc/CHANNEL_MANAGER.md`.
