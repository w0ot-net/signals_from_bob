# compat.py Performance Plan

## Goal
Reduce overhead in compat helpers by minimizing copies and extra view objects,
while preserving Python 2.7/3 compatibility and current external behavior.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Windows and Linux supported (ICMP remains Linux-only).
- Keep helper semantics stable unless all call sites are updated in the same change.
- ASCII only for code and scripts.

## Current Behavior (Problem Statement)
- `to_bytes()` always copies for `memoryview`/`bytearray` via `.tobytes()`.
- Py3 `require_bytes_like_or_bytearray()` returns a `memoryview` for `bytearray`,
  even though the docstring claims "without copying".
- `require_bytes_like()` uses `try/except TypeError` for the buffer protocol;
  invalid inputs pay exception cost.
- `buffer_view()` always creates a view and may slice even when the length is
  unchanged, creating extra objects in tight loops.

## Performance Opportunities
- Avoid unnecessary `to_bytes()` calls where bytes-like objects are sufficient.
- Reduce wrapper churn by returning `bytearray` unchanged when allowed.
- Avoid exception-as-control-flow if invalid inputs are common in hot paths.
- Avoid extra view objects when the requested length already matches.

## Options

### Option A: Align bytearray semantics and reduce wrappers (recommended)
- In Py3, return `bytearray` unchanged from `require_bytes_like_or_bytearray()`.
- Optionally return `bytearray` unchanged from `require_bytes_like()` after an
  audit of call sites for any `memoryview`-specific expectations.

### Option B: Separate "bytes-only" from "bytes-like" paths
- Keep `require_bytes_like()` as the default for bytes-like validation.
- Limit `to_bytes()` to true boundary points that require a `bytes` object
  (e.g., encoding, struct packing, hashing, or network write APIs).
- Update call sites that currently call `to_bytes()` but only need bytes-like
  access (slicing, indexing, length).

### Option C: Reduce exception overhead in `require_bytes_like()`
- Add early checks for common invalid types (text, int, None) before attempting
  `memoryview()` to avoid expensive exceptions.
- Keep the `try/except` for buffer-protocol types not on the fast path.

## Recommendation
Implement Option A plus Option B. Aligning bytearray handling removes wrapper
churn, and reducing `to_bytes()` usage removes avoidable copies. Option C can
follow if profiling shows a high rate of invalid inputs.

## Implementation Steps
1. Audit every `to_bytes()` call site to decide whether bytes are required or
   whether bytes-like data is sufficient.
2. Update `require_bytes_like_or_bytearray()` on Py3 to return `bytearray`
   unchanged; update any call sites that depend on a `memoryview`.
3. If safe, adjust `require_bytes_like()` on Py3 to return `bytearray`
   unchanged; otherwise introduce a targeted helper and use it where needed.
4. Remove unnecessary `to_bytes()` conversions in hot paths and keep
   conversions at explicit boundaries.
5. Tighten `buffer_view()` usage in ICMP checksum and other hot paths to avoid
   slices when the length already matches.
6. Update docstrings to document any semantic changes consistently across Py2/3.

## Tests
- Add unit coverage for:
  - `require_bytes_like_or_bytearray()` bytearray passthrough on Py3.
  - `to_bytes()` still returning `bytes` and rejecting text.
  - `buffer_view()` length handling and error behavior.
- Run fast unit tests with `python3`; do not run `tests/e2e/` locally.

## Affected Components
- sfb/compat.py
- sfb/crypto.py
- sfb/channel/channel.py
- sfb/protocol/segment.py
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/icmp/icmp_packet.py
- sfb/transport/icmp/icmp_client.py
- sfb/transport/icmp/icmp_server.py
- sfb/transport/memory/memory_client.py
- sfb/transport/memory/memory_server.py
- tests (compat helper unit coverage)
