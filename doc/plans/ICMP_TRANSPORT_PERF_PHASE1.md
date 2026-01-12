# ICMP Transport Perf Phase 1 - Client Hot Path

Status: draft

## Summary
Reduce ICMP client hot-path overhead by caching select inputs, avoiding
extra syscalls for non-blocking polls, and draining ready sockets to find
the first valid response without returning to select. Optionally reuse a
receive buffer on Python 3 to cut allocation churn.

## Goals
- Reduce per-call allocations and select overhead in the ICMP client.
- Treat would-block reads as "no data" without warning logs.
- Preserve ICMP semantics and pending tracking.

## Non-Goals
- Change protocol behavior or asymmetry semantics.
- Modify ICMP server behavior.
- Add new dependencies or run automated tests.

## Affected Components
- `sfb/transport/icmp/icmp_client.py`

## Helper Notes
- `parse_icmp_echo` already accepts bytes-like input on Python 3 via
  `compat.require_bytes_like`, so recvfrom_into should not require packet
  parsing changes.

## Plan
1. Cache select inputs and target addr in `__init__`.
   - Store `self._sock_list = [self._sock]` for select.
   - Store `self._target_addr = (self._target_ip, 0)` for sendto.
2. Add would-block detection to the client recv path.
   - Add a small `_get_errno` helper and a `_WOULD_BLOCK` set for
     EAGAIN/EWOULDBLOCK (plus WSAEWOULDBLOCK if present).
   - Treat would-block as `(None, None)` without warning logs.
3. Optimize `recv` when `timeout == 0`.
   - Attempt a direct non-blocking receive before calling select.
   - If would-block, return `(None, None)` immediately.
4. Drain after select readiness.
   - Loop on recv until would-block, returning the first valid response.
   - Skip malformed/oversize/missing-pending packets without returning to
     select.
   - Cap the drain loop (for example, 32 packets) to avoid starvation under
     constant noise.
5. Optional: reuse a receive buffer on Python 3.
   - Allocate a persistent bytearray sized to `_recv_bufsize`.
   - Use `recvfrom_into` and pass a length-limited view to the parser.
   - Keep Python 2 on `recvfrom` to avoid buffer handling quirks.

## Testing
- Do not run tests.
