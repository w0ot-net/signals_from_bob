# Channel Lock Split Fine-Grain Plan

Status: abandoned

## Summary
Split channel locking into separate locks for state, send buffer, send state,
recv buffer, and recv state. The goal is to cut hot-path contention between
reads, writes, and close transitions while preserving strict invariants.

## Goals
- Reduce lock contention between read/write paths and state transitions.
- Shorten lock hold times on send/recv hot paths.
- Preserve channel semantics and error behavior.
- Enforce strict lock ordering and fail fast on invariant violations.

## Non-Goals
- Change protocol/control messages.
- Alter channel manager packing policy.
- Add or run tests.
- Introduce shared lock arrays or lock striping across channels.

## Affected Components
- `sfb/channel/channel.py`
- `sfb/channel/control_channel.py`
- `sfb/channel/channel_manager.py`
- `sfb/modules/relay_pump.py` (only if we add new accessors)
- `doc/architecture/CHANNEL.md`
- `doc/architecture/CHANNEL_MANAGER.md`

## Plan
1. Define the lock model and ordering.
   - Add `self._state_lock`, `self._send_state_lock`, `self._send_buf_lock`,
     `self._recv_state_lock`, `self._recv_buf_lock`.
   - Lock order for multi-lock paths: state -> send_state -> send_buf
     -> recv_state -> recv_buf.
   - Do not hold any lock while invoking callbacks.
   - Fail fast on lock-order violations in debug paths.

2. Partition data by lock ownership.
   - State: `state`, `_error`, `_error_code`, `_open_event`, `_closed_event`.
   - Send state: `_send_closed`, `_close_pending`, `_half_close_pending`,
     `_send_state_seq`, `_send_space_event`.
   - Send buffer: `_send_buf`, `_send_buf_size`.
   - Recv state: `_recv_closed`, `_recv_event`.
   - Recv buffer: `_recv_buf`, `_recv_buf_size`.

3. Update send-side operations to use fine-grained locks.
   - `write`, `wait_send_space`, `write_wait`, `close_write`,
     `_take_send_data`, `_get_send_state`, `_has_send_data`, `send_buf_size`.
   - Use send_state_lock for state/closure checks, send_buf_lock for buffer
     mutation and size updates.
   - Keep `send_state_seq` monotonic and update events only under send_state_lock.

4. Update recv-side operations to use fine-grained locks.
   - `read`, `read_exact`, `_consume_recv`, `_deliver`, `_set_recv_closed`,
     `recv_buf_size`.
   - Use recv_state_lock for closed/error checks, recv_buf_lock for buffer
     mutation and size updates.
   - Ensure `_recv_event` transitions are under recv_state_lock.

5. Rework state transitions to preserve invariants.
   - `close`, `abort`, `_set_state` acquire state_lock first, then update
     send/recv state under their locks.
   - Drop buffers under send_buf_lock and recv_buf_lock separately, never
     holding both at once.
   - Ensure close/abort sets closed flags before releasing state_lock so new
     I/O cannot slip in.

6. Adjust control channel to the new locks.
   - Replace `self._lock` usage in `ControlChannel._take_send_data` with
     send_state_lock or helpers to query send-buffer empty state.
   - Keep send event transitions consistent with send_buf_size updates.

7. Review channel manager interactions.
   - Ensure manager lock is not held while taking any channel locks.
   - Confirm `_on_channel_send_state` only depends on send-state callbacks.

8. Update documentation.
   - `doc/architecture/CHANNEL.md`: document fine-grained lock ownership and
     ordering.
   - `doc/architecture/CHANNEL_MANAGER.md`: note per-channel send/recv locks
     are independent and callbacks are invoked without locks held.

## Notes on per-channel lock arrays
- Each Channel already owns its locks, which avoids cross-channel contention.
- Shared lock arrays would reintroduce contention between unrelated channels
  and complicate lifecycle tracking without clear wins at current channel
  counts (<= 255).

## Testing
- Do not run tests.

## Abandonment notes
- 2026-01-15: Abandoned per request; no implementation work recorded.
