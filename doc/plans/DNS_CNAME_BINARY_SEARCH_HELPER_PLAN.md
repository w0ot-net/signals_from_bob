# DNS CNAME Binary Search Helper Plan

## Goal
- Remove duplicated binary search loops used for CNAME payload caps.
- Centralize the search logic in a small helper to keep behavior consistent.

## Non-Goals
- Change MTU math, error handling, or label validation.
- Modify DNS name encoding/decoding behavior.
- Run tests here.

## Affected Components
- sfb/transport/dns/dns_codec.py

## Plan
1. Add a small helper `_binary_search_max(low, high, fits_fn)` in
   `sfb/transport/dns/dns_codec.py`:
   - `fits_fn(value)` returns True if the candidate fits.
   - Implement the current inclusive binary search and return the best value.
2. Update `_max_cname_payload_for_response()` to use the helper:
   - Keep the current try/except and `ValueError` handling inside the
     `fits_fn` closure.
3. Update `calc_cname_payload_cap()` to use the helper:
   - Preserve the early exits and fixed-length checks before the helper call.
   - Keep the existing behavior for `ValueError` when computing QNAME lengths.
4. Confirm behavior parity:
   - Ensure `low`, `high`, and `best` logic matches the current inclusive loop.
   - Verify that edge cases (no fits, max fits) return the same values.

## Testing
- Do not run tests here. The user will run tests with python3 if needed.
