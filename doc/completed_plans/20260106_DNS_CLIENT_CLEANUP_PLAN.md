# DNS Client Cleanup Plan

## Goal
- Remove duplicated error-handling branches in `DnsClient._try_recv`.
- Simplify a redundant guard in the qname mismatch check.
- Drop an unreachable negative clamp in `_query_payload_for_target`.
- Keep performance, logging fields, and runtime behavior unchanged.
- Preserve Python 2.7/3 compatibility and standard-library-only usage.

## Non-Goals
- Change DNS protocol behavior, MTU calculations, or retransmit logic.
- Alter logging event names or field structure.
- Update tests or run E2E tests under `tests/e2e` (user will run them).

## Affected Components
- sfb/transport/dns/dns_client.py

## Plan
1) Merge `qname is None` and `payload is None` handling into a single branch
   in `_try_recv` that logs `dns.error_response` once and performs the same
   pending cleanup.
2) Replace the redundant `pending is not None` guard in the qname mismatch
   check with a direct comparison to `pending.qname`.
3) Remove the negative target clamp in `_query_payload_for_target`, keeping
   the existing lookup bounds handling intact.

## Execution Notes
- Merged error handling into a single `error_response` branch while keeping
  the mismatched-qname early return intact.
- Dropped the redundant guard in the qname mismatch check.
- Removed the unreachable negative target clamp in `_query_payload_for_target`.
- Tests not run (per instructions).
