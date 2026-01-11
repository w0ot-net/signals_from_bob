# PendingTracker Prune Deadline Plan

Status: abandoned

## Summary
Reduce full-dictionary scans in PendingTracker.prune by tracking the next
possible expiry time and skipping scans until an entry can expire.

## Goals
- Avoid O(n) scans on every prune call when no entries can expire yet.
- Preserve PendingTracker behavior and public API.
- Keep changes confined to the transport base utility.

## Non-Goals
- Change transport semantics or timeout behavior.
- Add new configuration options.
- Add or run automated tests.

## Affected Components
- `sfb/transport/transport_base.py`

## Plan
1. Track the earliest expiry in `PendingTracker`.
   - Add `self._next_expiry = None` in `__init__`.
   - On `add`, compute `expiry = now + self._timeout` and set
     `_next_expiry = expiry` if it is None or the new expiry is earlier.
2. Skip pruning when no entries can expire.
   - In `prune`, if `_next_expiry` is not None and `now < _next_expiry`,
     return `[]` without scanning `_entries`.
3. Recompute the next expiry when a scan occurs.
   - When `prune` scans entries, compute the minimum expiry among remaining
     entries and store it in `_next_expiry` (or None if empty).
4. Keep `_next_expiry` safe on removals.
   - On `pop`, `clear`, and any code path that empties `_entries`, set
     `_next_expiry = None` so the next prune recomputes a safe value.
   - This may cause an extra scan but avoids skipping an actual expiry.

## Testing
- Do not run tests.
