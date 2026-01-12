# ICMP Transport Perf Phase 2 - Server Hot Path

Status: completed

## Summary
Improve the ICMP server receive path by draining the socket after select
and optionally reusing a receive buffer on Python 3. This reduces syscalls
and allocation churn when there is ICMP background noise or bursts.

## Goals
- Reduce per-request syscall and allocation overhead on the server.
- Avoid warning logs for would-block reads.
- Preserve ICMP request/response behavior.

## Non-Goals
- Change asymmetry behavior or polling semantics.
- Modify ICMP client behavior.
- Add new dependencies or run automated tests.

## Affected Components
- `sfb/transport/icmp/icmp_server.py`

## Helper Notes
- Bob throughput remains bounded by Alice's poll rate per
  `doc/architecture/ASYMMETRY.md`, so improvements here are incremental.
- `parse_icmp_echo` already accepts bytes-like input on Python 3 via
  `compat.require_bytes_like`, so recvfrom_into should not require packet
  parsing changes.

## Plan
1. Cache select inputs in `__init__`.
   - Store `self._sock_list = [self._sock]` for select.
2. Add would-block detection to the server recv path.
   - Add a small `_get_errno` helper and a `_WOULD_BLOCK` set for
     EAGAIN/EWOULDBLOCK (plus WSAEWOULDBLOCK if present).
   - Treat would-block as "no data" without warning logs.
3. Drain after select readiness.
   - After select indicates readiness, loop on recv until would-block.
   - Skip malformed/oversize packets and continue draining.
   - Return the first valid request to the caller.
4. Optional: reuse a receive buffer on Python 3.
   - Allocate a persistent bytearray sized to `_recv_bufsize`.
   - Use `recvfrom_into` and pass a length-limited view to the parser.
   - Keep Python 2 on `recvfrom` to avoid buffer handling quirks.

## Testing
- Do not run tests.

## Execution Notes (2026-01-11)
- Added cached socket list, would-block handling, and a drain loop in the ICMP
  server receive path to reduce select/syscall overhead under noise.
- Added optional Python 3 recvfrom_into buffer reuse with a bytearray backing
  buffer; Python 2 remains on recvfrom.
- No tests were run.
