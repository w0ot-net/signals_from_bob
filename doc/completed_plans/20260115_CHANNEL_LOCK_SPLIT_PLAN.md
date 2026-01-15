# Channel Lock Split Plan

Status: completed

## Summary
Split the Channel lock into send/recv locks plus a small state lock so
reads/deliveries no longer block writes/drains while keeping strict invariants.

## Goals
- Remove send/recv contention by isolating buffer locks.
- Preserve current channel semantics and error behavior.
- Enforce clear lock ordering and fail fast on invariant violations.

## Non-Goals
- Change channel protocol messages or control flow.
- Modify channel manager packing policy.
- Add or run tests.

## Affected Components
- `sfb/channel/channel.py`
- `sfb/channel/control_channel.py`
- `sfb/channel/channel_manager.py`
- `doc/architecture/CHANNEL.md`
- `doc/architecture/CHANNEL_MANAGER.md`
- `sfb/modules/relay_pump.py` (only if we add new accessors for state snapshot)

## Plan
1. Define the lock model and ordering.
   - Add `self._state_lock`, `self._send_lock`, `self._recv_lock`.
   - Document and enforce a single ordering: state -> send -> recv.
   - Prohibit taking `state_lock` while holding send/recv locks.

2. Partition channel data by lock ownership.
   - State lock: `state`, `_error`, `_error_code`, and state transitions.
   - Send lock: `_send_buf`, `_send_buf_size`, `_send_state_seq`,
     `_send_closed`, `_send_space_event`, `_close_pending`,
     `_half_close_pending`.
   - Recv lock: `_recv_buf`, `_recv_buf_size`, `_recv_closed`, `_recv_event`.
   - Keep callbacks invoked outside locks.

3. Update send-side operations to use `send_lock`.
   - `write`, `wait_send_space`, `write_wait`, `close_write`,
     `_take_send_data`, `_get_send_state`, `_has_send_data`, `send_buf_size`.
   - Use `state_lock` only for state checks that must not race with close.
   - Keep `send_state_seq` monotonic and update pending events under
     `send_lock`.

4. Update recv-side operations to use `recv_lock`.
   - `read`, `read_exact`, `_consume_recv`, `_deliver`, `_set_recv_closed`,
     `recv_buf_size`.
   - Ensure `recv_event` is set when data arrives or recv is closed.

5. Rework state transitions to preserve invariants.
   - `close`, `abort`, `_set_state` acquire `state_lock` then update
     send/recv closures under their respective locks.
   - Ensure closing sets `_send_closed`/`_recv_closed` before releasing
     `state_lock` so new I/O cannot slip in.
   - Keep `wait_open`/`wait_closed` semantics unchanged.

6. Adjust control channel to the new locks.
   - Replace `self._lock` usage in `ControlChannel._take_send_data` with
     `send_lock` or a small helper (e.g., `_send_buf_empty_locked`).
   - Confirm send event transitions remain consistent with `send_buf_size`.

7. Review channel manager interactions.
   - Verify no code holds the manager lock while acquiring channel locks.
   - Confirm `_on_channel_send_state` logic still only depends on send-state
     callbacks and remains deadlock-free.

8. Update documentation.
   - `doc/architecture/CHANNEL.md`: describe the new lock model and ordering.
   - `doc/architecture/CHANNEL_MANAGER.md`: note that channel send/recv locks
     are independent, and the manager relies on send-state callbacks.

9. Optional breaking simplification (if it reduces complexity).
   - Add explicit `send_closed`/`recv_closed` accessors and stop accessing
     private fields in `sfb/modules/relay_pump.py`.
   - If adopted, update all call sites in the same change.

## Testing
- Do not run tests.

## Execution Notes
- Added state/send/recv locks in Channel with defined ordering and split send/recv paths.
- Updated control channel send-event handling for the new send lock.
- Added send_closed/recv_closed accessors and used them in relay pump snapshots.
- Documented the lock model in channel and channel manager architecture docs.

Completed: 2026-01-15
