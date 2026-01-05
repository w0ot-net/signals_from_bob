# Channel Race Conditions Plan

## Summary
Prevent data loss when control messages and data segments arrive in the same
packet, and ensure stale send-state callbacks cannot affect a reused channel ID.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/channel/channel_manager.py
- doc/CHANNEL_RACE_CONDITIONS_PLAN.md

## Plan
1) Reorder delivery so control messages are applied before data segments.
   - In `BaseTunnel._handle_data`, split each ready packet's segments into
     control (channel 0) and data (non-zero) groups.
   - Deliver control segments first for the packet, then call
     `_process_control_messages()` to apply `open_ok` or other state changes.
   - Deliver data segments after control processing so newly opened channels
     are in `STATE_OPEN` before data arrives.
   - Keep keepalive handling unchanged and preserve per-packet ordering
     (control before data for the same packet).

2) Guard send-state callbacks against channel ID reuse.
   - In `ChannelManager._register_channel_locked`, wrap the send-state callback
     with a closure that captures the channel instance.
   - Update `_on_channel_send_state` to accept the channel instance and verify
     `self._channels.get(channel_id) is channel` before mutating
     `_active_channels` or `_send_state_seq`.
   - This prevents a late callback from a closed channel from affecting a
     newly allocated channel with the same ID when reuse cooldown is 0.

3) Document the ordering guarantee.
   - Add a short note in `doc/CHANNEL_MANAGER.md` describing that control
     messages are processed before data segments from the same packet to avoid
     open/data races.

## Validation
- Add or run a focused non-E2E test that injects `open_ok` plus data in the
  same packet and asserts data is delivered after the channel transitions to
  `STATE_OPEN`.
- Run existing non-E2E checks with `python3` if available.
- Do not run tests under `tests/e2e/`.

## Risks and Notes
- The control-before-data change should be safe because `close` and
  `half_close` messages are only sent after send buffers drain.
- The callback guard is low risk and preserves Python 2.7/3 compatibility using
  standard library constructs only.
- Keep behavior consistent on Windows and Linux.
