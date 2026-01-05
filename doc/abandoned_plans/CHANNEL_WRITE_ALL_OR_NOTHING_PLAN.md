# Channel Write All-or-Nothing Plan

Status: abandoned

## Summary

Make `Channel.write` queue all bytes or block until it can, eliminating partial
writes and the `write_wait`/`wait_send_space` backoff loops. Update socket-to-
channel pumps and other callers to rely on blocking writes and simplify their
state machines. This is a breaking change to backpressure semantics.

## Goals

- `Channel.write` is all-or-nothing: queue full payload or raise.
- Remove `write_wait`, `wait_send_space`, and channel write backoff settings.
- Simplify socket-to-channel pumps by removing pending buffers and backoff.
- Keep Python 2.7/3 compatibility and Windows/Linux support.
- Preserve existing muxer packing, control semantics, and MTU negotiation.

## Non-Goals

- Changing channel read semantics or control message parsing.
- Altering channel manager packing/ordering policies.
- Running E2E tests.

## Affected Components

- sfb/channel/channel.py
- sfb/channel/control_channel.py
- sfb/channel/channel_manager.py
- sfb/config.py
- sfb/log_profiles.py
- sfb/modules/relay_pump.py
- sfb/modules/nc_linux/nc_linux_pump.py
- sfb/modules/file_transfer/file_transfer.py
- doc/CHANNEL.md
- doc/LOGGING.md
- tests/test_channel.py
- tests/test_socks.py

## Plan

1. Redefine `Channel.write` semantics (all-or-nothing)
   - Add blocking behavior to `Channel.write` with an optional timeout so it
     waits until the full payload can be queued (no partial writes).
   - Decide the error for oversized payloads (`len(data) > max_send_buf`) and
     document it (likely `buffer_full` or a new `too_large` code).
   - Remove `write_wait` and `wait_send_space`, and delete write backoff fields
     from `Channel.__init__` and `__slots__`.
   - Keep `_send_space_event` as the wakeup signal, but drive it by full-payload
     availability rather than partial space.

2. Remove channel write backoff configuration plumbing
   - Drop `channel_write_backoff_*` from `sfb/config.py` and validation.
   - Update `channel_manager` and `control_channel` constructors to stop
     passing backoff settings.

3. Simplify socket-to-channel pumps
   - Relay pump: remove pending buffers, `buffer_full` backoff, and
     `wait_send_space` calls. After reading from the socket, call
     `Channel.write` in blocking mode (or with a timeout if needed) and adjust
     stats/logging to match the new flow.
   - nc_linux pump: same simplification; eliminate pending/backoff logic and
     rely on blocking `Channel.write`.
   - If backoff configuration becomes unused (for example,
     `relay_pump_backoff_max`), remove it and update validation/log profiles.

4. Update other callers that assumed partial writes
   - Replace `write_all` usage in file transfer with the new `Channel.write` API
     and keep timeout handling intact.
   - Audit remaining `Channel.write` call sites for partial-write handling and
     simplify them.

5. Documentation and logging cleanups
   - Update `doc/CHANNEL.md` to describe the new all-or-nothing write behavior.
   - Remove `channel.write_wait` from `doc/LOGGING.md` and log profiles.
   - Leave historical bug docs unchanged.

6. Tests
   - Update `tests/test_channel.py` to remove `wait_send_space` coverage and add
     coverage for blocking/timeout write behavior.
   - Update `tests/test_socks.py` to reflect the new `Channel.write` API.

## Validation

- Run `python3 -m unittest tests.test_channel`.
- Run `python3 -m unittest tests.test_socks`.
- Do not run tests under `tests/e2e/`.

## Abandonment notes

- 2026-01-05: Abandoned per request; no implementation work recorded.
