# Shared Method Name Selection Plan

Status: draft

## Summary
Centralize Python 2/3 method-name differences (for example, `tobytes` vs
`tostring`) into `sfb/compat.py` helpers and update call sites to use them.
Ensure every helper is actually used and no ad-hoc method-name selection
remains outside compat.

## Goals
- Reduce scattered PY2/PY3 method-name selection in the codebase.
- Preserve current behavior, performance, and logging semantics.
- Ensure compatibility helpers are used at all relevant call sites.

## Non-Goals
- Drop Python 2 support or introduce new dependencies.
- Change public APIs, wire formats, or logging schemas.
- Add or run tests here.

## Affected Components
- `sfb/compat.py`
- `sfb/channel/channel.py`
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_cert.py`

## Plan
1. Add explicit compat helpers for method-name differences:
   - Example: `bytes_from_buffer(value)` that returns bytes from `buffer`,
     `memoryview`, or `bytearray` using `tobytes` or `tostring` as needed.
   - Keep behavior identical to current call sites (type checks, errors).
2. Update call sites to use compat helpers instead of inline method selection:
   - Replace the `tobytes`/`tostring` branches in `_slice_view` with the new
     helper.
   - Replace the `PY2`-specific `bytearray.tostring()` usage in the TLS bump
     cert builder with the helper.
3. Ensure all name-selection helpers are actually used:
   - Run `rg` searches to confirm `tobytes`/`tostring` usage exists only in
     `sfb/compat.py`.
   - Confirm the new helper is referenced by all previously ad-hoc call sites.
4. Validate behavior by manual inspection of the updated code paths.

## Testing
- Do not run tests here.
