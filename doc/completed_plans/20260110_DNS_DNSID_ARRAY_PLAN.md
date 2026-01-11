# DNS DNS ID Array Plan

Status: completed

## Summary
Replace the per-packet DNS ID lookup dict with a fixed 65536-entry list to
avoid hash lookups on every send and receive.

## Goals
- Reduce per-packet overhead in the DNS client hot path.
- Preserve current behavior and error handling.
- Keep the change contained to the DNS client.

## Non-Goals
- Change protocol behavior or DNS message formats.
- Add or run automated tests.
- Modify server-side code paths.

## Affected Components
- `sfb/transport/dns/dns_client.py`

## Plan
1. Introduce a fixed-size lookup table in `sfb/transport/dns/dns_client.py`.
   - Replace `self._dns_to_corr = {}` with a list sized to the DNS ID space
     (`[None] * 65536`).
2. Update send/receive tracking.
   - On send, store the corr_id at `lookup[dns_id]`.
   - On receive, treat `None` as stale/unknown and otherwise use the corr_id.
   - When cleaning up (success, error, or prune), reset the entry to `None`.
3. Keep logging and behavior consistent.
   - Ensure existing log events fire on the same conditions.
   - Keep the stale/unknown handling identical to the dict behavior.

## Testing
- Do not run tests.

## Execution Notes
- Replaced DNS ID lookup dict with a fixed-size list and reset slots to None on
  completion, error, and prune cleanup.
- Tests not run (not requested).
