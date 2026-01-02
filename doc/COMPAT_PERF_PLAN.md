# compat.py Performance Plan

## Goal
Reduce overhead in compat helpers by minimizing copies and extra view objects,
while preserving Python 2.7/3 compatibility and current external behavior.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Windows and Linux supported (ICMP remains Linux-only).
- Breaking changes are acceptable when they improve cleanliness or performance;
  update all call sites in the same change.
- ASCII only for code and scripts.

## Current Behavior (Problem Statement)
- `to_bytes()` always copies for `memoryview`/`bytearray` via `.tobytes()`.
- `require_bytes_like()` uses `try/except TypeError` for the buffer protocol;
  invalid inputs pay exception cost.
- `buffer_view()` always creates a view and may slice even when the length is
  unchanged, creating extra objects in tight loops.
- Some call sites require real `bytes` for concatenation, text decoding, or
  immutable queueing (segment encoding, DNS name parsing, DNS A/TXT RDATA,
  in-memory transport queues, ICMP parse payload).

## Performance Opportunities
- Avoid unnecessary `to_bytes()` calls where bytes-like objects are sufficient.
- Reduce wrapper churn by returning `bytearray` unchanged when allowed.
- Avoid exception-as-control-flow if invalid inputs are common in hot paths.
- Avoid extra view objects when the requested length already matches.
- Make bytes-only boundaries explicit to avoid implicit copies.

## Options

### Option A: Align bytearray semantics and reduce wrappers (defer)
- In Py3, return `bytearray` unchanged from `require_bytes_like_or_bytearray()`.
- Optionally return `bytearray` unchanged from `require_bytes_like()` after an
  audit of call sites for any `memoryview`-specific expectations or slicing
  behavior that could introduce extra copies.
- If `require_bytes_like()` returns `bytearray`, update `to_bytes()` to handle
  it without calling `.tobytes()`.

### Option B: Separate "bytes-only" from "bytes-like" paths
- Keep `require_bytes_like()` as the default for bytes-like validation.
- Limit `to_bytes()` to true boundary points that require a `bytes` object
  (e.g., encoding, struct packing, hashing, or network write APIs).
- Update call sites that currently call `to_bytes()` but only need bytes-like
  access (slicing, indexing, length).
- Keep `to_bytes()` at known bytes-only boundaries:
  - Segment encode/pack (bytes concatenation).
  - DNS name parsing/encoding and A/TXT RDATA handling (ASCII decode/encode).
  - Transport boundaries that promise immutable bytes to callers or queues
    (in-memory transport request/response queues, ICMP parse payload).
  - Any transport send path that relies on immutable bytes semantics.

### Option C: Reduce exception overhead in `require_bytes_like()` (defer)
- Add early checks for common invalid types (text, int, None) before attempting
  `memoryview()` to avoid expensive exceptions.
- Keep the `try/except` for buffer-protocol types not on the fast path.

## Recommendation
Implement Option B only. Keep `to_bytes()` at explicit bytes-only boundaries,
and avoid it where bytes-like objects are sufficient. Defer Option A and Option C
unless profiling shows they are required.

## Implementation Steps
1. Audit every `to_bytes()` call site and tag it as bytes-only or bytes-like;
   keep bytes-only boundaries and remove conversions elsewhere.
2. Replace non-boundary `to_bytes()` calls with `require_bytes_like()` (or local
   validation) and defer conversion to explicit bytes-only boundaries.
3. Keep `to_bytes()` at explicit bytes-only boundaries (segment encode/pack,
   DNS decoding/encoding, in-memory transport queueing, ICMP parse payload,
   and any immutable-bytes transport sends).
4. Update docstrings to document any semantic changes consistently across Py2/3.

## Tests
- Add unit coverage for:
  - `to_bytes()` on `bytearray` and `memoryview`, and rejection of text.
  - `crypto._require_key()` accepting bytes-like and rejecting text.
- Run fast unit tests with `python3`; do not run `tests/e2e/` locally.

## Affected Components
- sfb/compat.py
- sfb/crypto.py
- sfb/channel/channel.py
- sfb/protocol/segment.py
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/icmp/icmp_packet.py
- sfb/transport/icmp/icmp_client.py
- sfb/transport/icmp/icmp_server.py
- sfb/transport/memory/memory_client.py
- sfb/transport/memory/memory_server.py
- tests (compat helper unit coverage)
