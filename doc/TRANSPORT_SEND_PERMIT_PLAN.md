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
  - It does not mutate pending state; it only checks capacity.
- Make `Transport.send(self, data, permit)` a concrete method that enforces:
  - `permit` is present, matches the transport, and is unused.
  - Calls `_send_impl(self, data, permit)` (new abstract method).
- Remove `can_send()` from the transport interface and update call sites to
  use `reserve_send()` + `send()` only.
- Make `pending_count()` non-pruning and document that pruning only occurs in
  `reserve_send()` and `recv()`.

## Enforcement in Base Class (No Thin Wrappers)

- Introduce a `SendPermit` object with fields like:
  - `transport_id` (binds permit to one instance)
  - `now` (timestamp reused for log fields)
  - `pending_before` (count used for logging)
  - `used` (boolean to prevent reuse)
- Implement `Transport.send()` in the base class and do not allow subclasses
  to override it.
- Use a custom metaclass that rejects subclasses which define `send` in their
  class dict (raise `TypeError` at class creation).

## Detailed Implementation Plan

1. `sfb/transport/transport_base.py`
   - Add `SendPermit` class (small object or `namedtuple`).
   - Add abstract `reserve_send(now=None)` and `_send_impl(data, permit)`.
   - Implement `Transport.send(data, permit)` with validation and `used` guard.
   - Add a metaclass that forbids overriding `send`.
   - Update docstrings to explain the new flow.

2. `sfb/transport/dns/dns_client.py`
   - Implement `reserve_send()` using `prune_and_count` and `_on_prune`.
   - Move existing send logic into `_send_impl()`.
   - Use `permit.pending_before` for logging instead of `pending_count()`.
   - Remove `can_send()` from this class.

3. `sfb/transport/icmp/icmp_client.py`
   - Same pattern as DNS: `reserve_send()` + `_send_impl()`.
   - Use permit values for logging.
   - Remove `can_send()` from this class.

4. `sfb/transport/memory/memory_client.py`
   - Implement `reserve_send()` with a simple count check.
   - Move send logic to `_send_impl()` and pass permit for logging.

5. `sfb/transport/lossy.py`
   - Implement `reserve_send()` using the wrapper's `pending_count()` which
     includes dropped ids (no inner `pending_count()` call).
   - When forwarding to inner transport, call its `reserve_send()` to obtain
     a permit and pass it to `inner.send()`.
   - If a drop is simulated, do not call `inner.send()` and do not consume the
     inner permit; rely on wrapper `pending_count()` for capacity.

6. `sfb/tunnel/alice_tunnel.py`
   - Replace `transport.can_send()` with `transport.reserve_send(now=now)`.
   - Thread the permit into `_send_new_packet()` and `_send_retransmit()`.
   - Ensure one permit is used for exactly one send attempt.

7. Docs
   - Update `doc/TRANSPORTS.md` to describe the reserve/send contract.
   - Update `doc/DNS_TRANSPORT.md` and `doc/ICMP_TRANSPORT.md` with the new
     flow and pruning rules.
   - Note the breaking API change and required updates for external transports.

8. Tests
   - Add unit tests for DNS and ICMP to count prune calls per send attempt
     (wrap `PendingTracker.prune` and assert one call).
   - Update transport tests to pass a permit to `send()`.
   - Do not run E2E tests; they remain unchanged.

## Compatibility and Risks

- This is a hard break for any out-of-tree transports or callers that invoke
  `send()` directly without reserving.
- Reserve and send must be adjacent in time to avoid drift in pending counts.
- Permit reuse must be rejected to avoid bypassing capacity enforcement.

## Expected Outcome

- One prune per send attempt on DNS and ICMP.
- Clear, enforced send path with no thin wrappers.
- Consistent behavior across transports and wrappers.
