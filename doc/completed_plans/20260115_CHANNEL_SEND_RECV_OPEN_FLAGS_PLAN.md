# Channel Send/Recv Open Flags Plan

Status: complete

## Summary
Add explicit send/recv open flags in Channel to avoid taking the state lock on
hot write/read/deliver paths while keeping existing semantics and lock
ordering.

## Goals
- Reduce state_lock churn in write/read/deliver hot paths.
- Preserve channel semantics (error codes, close/half-close behavior).
- Keep strict lock ordering and fail fast on invariant violations.

## Non-Goals
- Change protocol/messages or channel manager packing.
- Add or run tests.

## Affected Components
- `sfb/channel/channel.py`
- `doc/architecture/CHANNEL.md`

## Plan
1. Define new flags and invariants in Channel.
   - Add `_send_open` and `_recv_open` to `__slots__` and initialize to False.
   - Invariants:
     - `_send_open` is True only when state is OPEN and send is not closed.
     - `_recv_open` is True only when state is OPEN/CLOSING and recv is not closed.
   - Mutate these flags only under the corresponding send/recv locks.

2. Update state transitions to keep flags consistent.
   - In `_set_state`, set/clear send/recv open flags while holding
     `state_lock` then `send_lock` and `recv_lock` in order.
   - On `STATE_OPEN`, set both flags True (unless already closed).
   - On `STATE_CLOSING`, set `_send_open` False and keep `_recv_open` True if
     the receive side is still open.
   - On `STATE_CLOSED`, clear both flags and mark send/recv closed.
   - In `close()` and `close_write()`, update `_send_open` before releasing
     `state_lock`.
   - In `_set_recv_closed`, clear `_recv_open` under `recv_lock`.

3. Convert hot paths to use open flags instead of the state lock.
   - `write`/`wait_send_space`: under `send_lock`, check `_send_closed` then
     `_send_open` for `not_open`, skipping the state lock on success paths.
   - `write_wait`: rely on `write()` errors and remove the extra state check.
   - `_deliver`: under `recv_lock`, drop when `_recv_open` is False without
     taking the state lock.
   - `read`: when the recv buffer is empty, check `_recv_closed` under
     `recv_lock` and consult `state_lock` only if `_closed_event` is set to
     retrieve error details.

4. Update documentation.
   - Document the new open flags and invariants in `doc/architecture/CHANNEL.md`.
   - Note that hot-path operations avoid the state lock and only consult it on
     close/error paths.

## Testing
- Do not run tests.

## Execution Notes
- Added send/recv open flags and kept transitions consistent across close paths.
- Updated hot-path operations to rely on open flags and reduce state lock usage.
- Documented open flag invariants and hot-path locking; tests not run.
