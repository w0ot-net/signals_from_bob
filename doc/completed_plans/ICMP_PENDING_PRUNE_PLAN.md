# Pending Prune Consistency Plan

## Status

Superseded by `doc/completed_plans/TRANSPORT_SEND_PERMIT_PLAN.md` which moves
pruning into `reserve_send()` and replaces the `can_send()`/`send()` flow with
permit enforcement.

## Goal

Reduce redundant O(n) pending pruning on transport send paths and make pruning
behavior consistent across transports that use PendingTracker (ICMP, DNS),
while preserving observable behavior (limits, logging, and timeouts).

## Current Behavior

- ICMP `send()` calls `can_send()` which calls `pending_count()` which calls
  `_prune_stale()`.
- ICMP `send()` then calls `_prune_stale()` again before sending.
- ICMP `send()` logs with `pending_count()` which prunes a third time.
- DNS mirrors the same pattern (plus `_dns_to_corr` cleanup on prune).
- Each prune iterates the full pending set and allocates a list of entries.
- `pending_count()` is used by tunnel logic, so it must still prune stale
  entries to avoid pending exhaustion.

## Plan

### Option A: Change the Transport interface (base class)

- Add optional `now` to `pending_count()`/`can_send()` and add a default
  `can_send()` in `Transport`.
- Pros: consistent interface, allows a single timestamp per tick.
- Cons: wide changes across transports, tests, and docs; base class still does
  not own pending state; send paths still need refactors to pass `now`.

### Option B: Extend PendingTracker

- Add `prune_and_count(now)` or cached count inside `PendingTracker`.
- Pros: central helper with minimal per-transport code.
- Cons: cannot handle transport-specific cleanup (DNS) without callbacks; any
  caching in the tracker changes semantics for all users.

### Option C: Shared helper/mixin under sfb/transport (recommended)

- Add a helper module (or mixin in `transport_base`) that:
  - Calls a provided `prune_fn(now)` so transports keep their existing logging.
  - Accepts an optional `on_prune(stale)` callback for extra cleanup.
  - Returns the post-prune count and allows reusing one `now` for pruning.
- Update ICMP and DNS to use the helper in `pending_count()` and `send()`.
- Keep the public `Transport` interface unchanged.

### Recommendation

Use Option C to keep the interface stable while making ICMP and DNS behavior
consistent and avoiding redundant O(n) work.

### Implementation Sketch

1. Add helper:
   - `prune_and_count(pending, prune_fn, now=None, on_prune=None)` -> count
   - or a small `PendingPruner` class with `count(now)` and `prune(now)`
2. ICMP client:
   - `pending_count()` calls helper (prunes once via `_prune_stale`).
   - `send()` captures `now`, gets `pending_before`, enforces `max_in_flight`,
     adds pending using the current time (default `PendingTracker.add` or a
     `now_add` after `sendto`), logs `pending_before + 1`.
   - Ensure `send()` does not call `can_send()` or `pending_count()` after the
     helper runs; it should use `pending_before` for `max_in_flight` checks to
     avoid a second prune in the same send path.
3. DNS client:
   - Same pattern, with `on_prune` callback to clear `_dns_to_corr`.
4. Docs:
   - Update `doc/DNS_TRANSPORT.md` pruning description to note that pruning is
     shared within a single send when a cached `now` is used.
   - If interface changes are chosen, update `doc/TRANSPORTS.md`.
5. Tests:
   - Add unit tests for ICMP and DNS that stub `PendingTracker.prune` (wrapping
     and restoring the original) to assert one prune per `send()`.
   - Verify `pending_count()` still removes stale entries.

## Tests

- Add unit tests for ICMP and DNS that stub `PendingTracker.prune` (wrapping
  and restoring the original) to assert one prune per `send()`.
- Verify `pending_count()` still removes stale entries.
- For DNS, assert the prune callback clears `_dns_to_corr` for stale entries.
- Avoid raw ICMP sockets by stubbing `_sock.sendto` and `build_echo_request`,
  reusing the existing test setup that bypasses raw socket privilege checks.

## Notes

- No wire format changes.
- Keep Linux-only ICMP constraints intact.
- Keep the Transport interface stable unless Option A is explicitly chosen.

## Execution Notes

- Added `prune_and_count` helper in `sfb/transport/transport_base.py` and used
  it in ICMP/DNS `pending_count()` and `send()` so each send prunes once with a
  shared timestamp; DNS uses `on_prune` to clean `_dns_to_corr`.
- Updated `doc/DNS_TRANSPORT.md` to document the single-prune-per-send behavior.
- Added unit tests in `tests/test_icmp_client.py` and
  `tests/test_dns_client_server.py` to verify one prune per send and that
  `pending_count()` prunes stale entries.
- Tests not run here.
