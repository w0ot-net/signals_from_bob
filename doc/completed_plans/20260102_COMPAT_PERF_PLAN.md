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
- In Py3, `to_bytes()` always copies for `memoryview`/`bytearray` via
  `.tobytes()`.
- In Py2, `to_bytes()` accepts `memoryview`/`buffer` and copies to bytes; it
  rejects text.
- `require_bytes_like()` uses `try/except TypeError` for the buffer protocol;
  invalid inputs pay exception cost.
- `buffer_view()` always creates a view and may slice even when the length is
  unchanged, creating extra objects in tight loops.
- Some call sites require real `bytes` for concatenation, text decoding, or
  immutable queueing (segment encoding, DNS name parsing, DNS A/TXT RDATA,
  in-memory transport queues, ICMP parse payload, Plain cipher return value).

## Performance Opportunities
- Avoid unnecessary `to_bytes()` calls where bytes-like objects are sufficient.
- Make bytes-only boundaries explicit and documented to avoid implicit copies.
- Prefer bytes-like storage/processing internally, and convert to bytes at the
  last possible boundary where required.

## Options

### Option A: Align bytearray semantics and reduce wrappers (defer)
- In Py3, return `bytearray` unchanged from `require_bytes_like_or_bytearray()`.
- Optionally return `bytearray` unchanged from `require_bytes_like()` after an
  audit of call sites for any `memoryview`-specific expectations or slicing
  behavior that could introduce extra copies.
- If `require_bytes_like()` returns `bytearray`, update `to_bytes()` to handle
  it without calling `.tobytes()`.

### Option B: Bytes-only boundaries plus call site refactors
- Keep `require_bytes_like()` as the default for bytes-like validation.
- Limit `to_bytes()` to true boundary points that require a `bytes` object
  (encoding/decoding, bytes concatenation, immutable queueing).
- For every `to_bytes()` call site, decide bytes-only vs bytes-like and change
  call sites aggressively when it removes copies cleanly.
- Candidate refactors if they are net wins:
  - `Segment` stores bytes-like; `encode()` converts to bytes for concatenation.
  - `Plain.encrypt()` continues to return bytes; convert at the boundary, not
    earlier.
  - DNS decode/encode and TXT/A RDATA remain bytes-only boundaries.
  - ICMP parse payload remains bytes-only boundary.
  - In-memory transport queues remain bytes-only boundaries for immutability.
- Explicitly avoid adding `to_bytes()` in socket send paths (buffer protocol is
  accepted).

### Option C: Reduce exception overhead in `require_bytes_like()` (defer)
- Add early checks for common invalid types (text, int, None) before attempting
  `memoryview()` to avoid expensive exceptions.
- Keep the `try/except` for buffer-protocol types not on the fast path.

## Breaking-Change Analysis
- `Segment` storing bytes-like is not clean: control parsing/logging requires
  bytes for `.split`/`.decode` and would force conversions at use sites
  (`sfb/tunnel/alice_tunnel.py`, `sfb/protocol/__init__.py`), erasing copy
  savings. Py2 also has weaker bytearray/memoryview concatenation behavior.
- Channel send buffers are bytes-only for immutability. Dropping `to_bytes()`
  would allow caller mutation of queued data, which is a visible behavior
  change. Preserving semantics would still require a copy, so there is no win.
- In-memory transport queues already require bytes for immutability; moving
  them to bytes-like would be a regression with no clear performance gain.
- Returning `bytearray` directly from `require_bytes_like()` in Py3 is a public
  type change with limited benefit; defer unless profiling shows this path is
  hot and safe for all callers.

## Recommendation
Implement Option B but keep bytes-only boundaries (segments, channel queues,
memory queues, DNS/ICMP/TXT/A decode). If the audit shows no meaningful wins,
stop and reassess Option C or Option A based on profiling.

## Implementation Steps
1. Inventory every `to_bytes()` call site and any `bytes` concatenation or
   decoding site; tag each as bytes-only or bytes-like.
2. For bytes-like sites, remove `to_bytes()` and update adjacent call sites to
   accept bytes-like inputs; keep return types unchanged where documented.
3. For bytes-only boundaries, keep or add `to_bytes()` and document the
   boundary contract in docstrings. Treat channel send buffers as bytes-only
   queues for immutability.
4. Update docs for any semantic changes consistently across Py2/3.
5. If the above yields no measurable reduction in copies, consider Option C or
   Option A as a follow-up change (with a fresh audit).

## Tests
- Add unit coverage for:
  - `to_bytes()` on `bytearray` and `memoryview` in Py3; accept `memoryview` in
    Py2; reject text on both.
  - `crypto._require_key()` accepting bytes-like and rejecting text.
  - `Plain.encrypt()` returning bytes for bytes-like inputs (if unchanged).
- Run fast unit tests with `python3`; do not run `tests/e2e/` locally.

## Affected Components
- sfb/compat.py
- sfb/crypto.py
- sfb/protocol/segment.py
- sfb/protocol/__init__.py
- sfb/channel/channel.py
- sfb/tunnel/alice_tunnel.py
- sfb/transport/dns/codec.py
- sfb/transport/icmp/icmp_packet.py
- sfb/transport/memory/memory_client.py
- sfb/transport/memory/memory_server.py
- tests (compat helper unit coverage)
- doc/completed_plans/COMPAT_PERF_PLAN.md

## Execution Notes (Option B, Guardrails)
- Audited `to_bytes()` call sites; all are bytes-only boundaries (segment
  encoding, DNS/ICMP decoding, channel send buffers, memory queues, and
  Plain cipher output), so they remain in place to preserve Py2
  memoryview/buffer acceptance.
- Tightened Py3 bytes-like validation to itemsize-1 buffers in
  `require_bytes_like()` and `buffer_view()`, with channel buffer checks
  rejecting non-byte memoryviews.
- Added unit coverage for `to_bytes()` conversions/rejections, Py3 non-byte
  memoryview rejection, and crypto bytes-like key handling with Plain passthrough.
