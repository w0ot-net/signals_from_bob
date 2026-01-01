# ICMP Pending Prune Plan

## Goal

Reduce redundant O(n) pending pruning on the ICMP send path while preserving
observable behavior (limits, logging, and timeouts).

## Current Behavior

- `send()` calls `can_send()` which calls `pending_count()` which calls
  `_prune_stale()`.
- `send()` then calls `_prune_stale()` again before sending.
- `send()` logs with `pending_count()` which prunes a third time.
- Each prune iterates the full pending set and allocates a list of entries.

## Plan

1. Add a helper that prunes once and returns the post-prune count, accepting an
   optional `now` so a single timestamp can be reused.
2. Update `pending_count()` to accept an optional `now` and delegate to the
   helper, keeping the default behavior of pruning when called externally.
3. Refactor `send()` to:
   - Capture `now = time.time()` once.
   - Prune once and store `pending_before`.
   - Enforce `max_pending` with `pending_before`.
   - Add the pending entry using the same `now`.
   - Log using the cached count (`pending_before + 1`).
4. Keep `can_send()` semantics for external callers (e.g., tunnel) by using
   `pending_count()` with pruning. Avoid calling `can_send()` from `send()` so
   the send path only prunes once.

## Tests

- Add a unit test that stubs `PendingTracker.prune` to count calls and asserts
  `send()` only triggers one prune per invocation.
- Add a unit test that verifies `pending_count()` still prunes stale entries.
- Avoid raw ICMP sockets by stubbing `_sock.sendto` and `build_echo_request`
  in the test fixture.

## Notes

- No wire format changes.
- Keep Linux-only ICMP constraints intact.
- Consider applying the same pattern to DNS later for consistency, but keep the
  initial change scoped to ICMP.
