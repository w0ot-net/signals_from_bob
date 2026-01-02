# Transport Send Permit Plan

## Goal

Enforce a single prune per send attempt by making send require a permit that is
issued once per attempt. This is a breaking change that moves enforcement into
the Transport base class and avoids thin wrappers.

## Current Behavior

- DNS and ICMP `can_send()` call `pending_count()` which prunes stale entries.
- DNS and ICMP `send()` prune again before sending.
- Tunnel code often calls `can_send()` and then `send()`, so pruning happens
  twice per request on the hot path.
- Lossy transport and other wrappers may add more `pending_count()` calls.

## Proposed Interface (Breaking)

- Add `reserve_send(now=None)` to `Transport` (abstract).
  - This performs prune + capacity check and returns a `SendPermit`.
  - If capacity is exhausted, return `None` (no exception).
  - It does not add pending entries; pruning is the only mutation.
  - A successful reserve counts against capacity until its permit is used;
    capacity checks must include outstanding permits.
- Make `Transport.send(self, data, permit)` a concrete method that enforces:
  - `permit` is present, matches the transport, and is unused.
  - Marks the permit as used before calling `_send_impl(self, data, permit)`
    (new abstract method).
- Remove `can_send()` from the transport interface and update call sites to
  use `reserve_send()` + `send()` only.
- Make `pending_count()` non-pruning and remove the `now` parameter; pruning
  only occurs in `reserve_send()` and `recv()`.
- Preserve "send blocked" logging by moving it into `reserve_send()` for
  transports that currently log inside `send()`.

## Enforcement in Base Class (No Thin Wrappers)

- Introduce a mutable `SendPermit` object with fields like:
  - `transport` (binds permit to one instance)
  - `now` (timestamp reused for log fields)
  - `pending_before` (count used for logging)
  - `used` (boolean to prevent reuse)
- Implement `Transport.send()` in the base class and do not allow subclasses
  to override it.
- Track outstanding permits in the base class and ensure `reserve_send()`
  includes them in capacity checks and `send()` clears them even on errors.
- Use a Py2/3-compatible custom metaclass that rejects subclasses which
  define `send` in their class dict (raise `TypeError` at class creation),
  while exempting the base `Transport` class itself.

## Detailed Implementation Plan

1. `sfb/transport/transport_base.py`
   - Add `SendPermit` as a small mutable class (e.g., `__slots__`) so `used`
     can be toggled in Py2/3.
   - Add abstract `reserve_send(now=None)` and `_send_impl(data, permit)`.
   - Add `self._reserved` (set of active permits) and a helper to register
     a permit once capacity is available.
   - Implement `Transport.send(data, permit)` with validation, mark `used`
     before calling `_send_impl`, and always remove the permit from
     `self._reserved` (even if `_send_impl` raises). Raise `TransportError`
     if permit is missing, invalid, or reused.
   - Add `TransportMeta(abc.ABCMeta)` and apply it via a local Py2/3
     `with_metaclass` helper so the override check works in both versions,
     and does not reject the base class definition.
   - Update docstrings: `reserve_send()` returns `None` on capacity;
     `pending_count()` is non-pruning and no longer accepts `now`.

2. `sfb/transport/dns/dns_client.py`
   - Implement `reserve_send()` using `prune_and_count` and `_on_prune`.
   - Include outstanding permits (`len(self._reserved)`) in capacity checks.
   - Move "send blocked" logging into `reserve_send()` using
     `permit.pending_before` (or the returned count) and keep the same log keys.
   - Move existing send logic into `_send_impl()`.
   - Use `permit.pending_before` for logging instead of `pending_count()`.
   - Remove `can_send()` from this class.
   - Make `pending_count()` return `len(self._pending)` without pruning.
   - Remove `now` parameter from `pending_count()` signature.

3. `sfb/transport/icmp/icmp_client.py`
   - Same pattern as DNS: `reserve_send()` + `_send_impl()`.
   - Include outstanding permits (`len(self._reserved)`) in capacity checks.
   - Move "send blocked" logging into `reserve_send()` and keep log keys.
   - Use permit values for logging.
   - Remove `can_send()` from this class.
   - Make `pending_count()` return `len(self._pending)` without pruning.
   - Remove `now` parameter from `pending_count()` signature.

4. `sfb/transport/memory/memory_client.py`
   - Implement `reserve_send()` with a simple count check (no pruning).
   - Include outstanding permits (`len(self._reserved)`) in capacity checks.
   - Move "send blocked" logging into `reserve_send()` and keep log keys.
   - Move send logic to `_send_impl()` and pass permit for logging.

5. `sfb/transport/lossy.py`
   - Implement `reserve_send()` using the wrapper's `pending_count()` (includes
     dropped ids) and prune expired dropped ids inside `reserve_send()`.
   - Include outstanding permits (`len(self._reserved)`) in capacity checks.
   - Decide drop/corrupt/duplicate inside `reserve_send()` and store the
     outcome (and any inner permits) on the wrapper permit to avoid extra
     inner prune calls.
   - If the impairment result is "send", acquire an inner permit inside
     `reserve_send()` and store it; if not available, return `None`.
   - If duplication is selected, request a second inner permit in
     `reserve_send()`; if unavailable, keep the primary send and skip the
     duplicate.
   - In `_send_impl()`, use the cached outcome and permits:
     - If dropped/corrupted, return a fake corr_id and record it; do not call
       `inner.reserve_send()` or `inner.send()`.
     - If sending, call `inner.send(data, inner_permit)`.
     - If duplication is selected and a second permit was stored, call
       `inner.send(data, dup_permit)`; otherwise skip the duplicate.

6. `sfb/tunnel/alice_tunnel.py` and all other call sites
   - Replace `transport.can_send()` with `transport.reserve_send(now=now)` and
     propagate the returned permit through the send path.
   - Thread the permit into `_send_new_packet()` and `_send_retransmit()`; if
     `reserve_send()` returns `None`, log and skip the send attempt.
   - Update direct `transport.send()` call sites (SYN/SYN-ACK/ACK paths and any
     other direct sends) to obtain a permit first.
   - Only replace `Transport.can_send()` uses; do not touch pacer or rate
     limiter `can_send()` calls.
   - Ensure one permit is used for exactly one send attempt.
   - Audit the repo for `can_send(`, `transport.send(`, and `pending_count(`)
     to update all call sites, including Bob-side modules.

7. Docs
   - Update `doc/TRANSPORTS.md` to describe the reserve/send contract.
   - Update `doc/DNS_TRANSPORT.md` and `doc/ICMP_TRANSPORT.md` with the new
     flow and pruning rules.
   - Note the breaking API change and required updates for external transports.
   - Remove any `pending_count(now=...)` examples from docs.

8. Tests
   - Add unit tests for DNS and ICMP to count prune calls per send attempt
     (wrap `PendingTracker.prune` and assert one call).
   - Update tests that pass `now` to `pending_count()` to call without
     arguments.
   - Move pruning assertions from `pending_count()` tests to `reserve_send()`
     or `recv()` tests.
   - Update transport tests to pass a permit to `send()`.
   - Update mock `Transport` subclasses in tests to implement `reserve_send()`
     and `_send_impl()` instead of `send()`.
   - Add base transport tests for invalid permit, wrong transport, and reuse.
   - Add tests that outstanding permits count against capacity, and that
     `send()` clears reservations even when `_send_impl` raises.
   - Update lossy transport tests to use permits and validate no extra inner
     `reserve_send()` calls per send attempt.
   - Do not run E2E tests; they remain unchanged.

## Compatibility and Risks

- This is a hard break for any out-of-tree transports or callers that invoke
  `send()` directly without reserving.
- This is a hard break for any out-of-tree transports or callers that pass
  `now` to `pending_count()`, which is removed.
- Lossy duplication may be skipped under capacity limits because it requires
  a second inner permit.
- Reserve and send must be adjacent in time to avoid drift in pending counts.
- Permit reuse must be rejected to avoid bypassing capacity enforcement.
- Callers that reserve and do not send will hold capacity until cleared; keep
  reserve/send adjacent or add explicit cleanup if needed.

## Expected Outcome

- One prune per send attempt on DNS and ICMP.
- Clear, enforced send path with no thin wrappers.
- Consistent behavior across transports and wrappers.
