# ICMP Checksum Performance Plan

## Goal
Reduce per-packet CPU cost in ICMP checksum calculation by eliminating
per-part array allocations and minimizing Python-level work, while preserving
wire format and Python 2.7/3 compatibility.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Linux and Windows (ICMP remains Linux-only).
- Preserve existing ICMP Echo Request/Reply wire format and parsing.
- Keep public transport interfaces stable unless a clean replacement is
  implemented with all call sites updated in the same change.

## Current Behavior (Problem Statement)
- `build_echo_packet()` calls `_checksum_parts((header, payload))`.
- `_checksum_parts` allocates an `array('H')` per part and calls `sum(words)`
  in Python for each, which caps PPS at higher rates.
- The header is packed twice and the payload is still copied once when
  `header + payload` is built for the final packet.

## Options

### Option A: Single-buffer checksum with in-place header (recommended)
- Build the final packet once using a `bytearray` and `struct.pack_into`.
- Compute checksum on the contiguous buffer in one pass, then patch the
  checksum field in place.
- Use a new helper that operates on a `memoryview`/buffer:
  - Python 3: use `memoryview.cast('H')` to avoid per-call `array` allocations.
  - Python 2: fall back to `array('H')` with `frombytes` (single allocation).
- Remove `_checksum_parts` or make it a thin wrapper that delegates to the
  new buffer-based checksum for callers that still pass parts.

### Option B: Reuse scratch arrays per call site
- Keep `_checksum_parts` but reuse a preallocated `array('H')` to reduce
  allocations.
- Requires care with concurrency and is still Python-heavy per packet.

### Option C: Kernel checksum offload via datagram ICMP
- Investigate switching to `SOCK_DGRAM` ICMP sockets on Linux and relying on
  the kernel to fill the checksum.
- Larger behavior change; may require payload-only writes or changes to
  raw socket privileges. Requires careful validation.

## Recommendation
Implement Option A first. It delivers the biggest win with minimal behavioral
change and keeps the ICMP transport semantics intact.

## Implementation Sketch
1. Add a buffer-based checksum helper in `sfb/transport/icmp/icmp_packet.py`:
   - Accept a buffer or `memoryview`.
   - Handle odd length bytes and endianness.
   - Python 3 fast path with `memoryview.cast('H')`.
   - Python 2 fallback with `array('H')` and `array_frombytes`.
2. Update `build_echo_packet()`:
   - Allocate a `bytearray` sized for header + payload.
   - `struct.pack_into('>BBHHH', buf, 0, icmp_type, ICMP_CODE, 0, ident, seq)`.
   - Copy payload into the buffer.
   - Compute checksum on the buffer and patch it with `pack_into`.
   - Return `bytes(buf)` (or `str` in Python 2 via existing compat helper).
3. Remove or refactor `_checksum_parts` to avoid per-part allocation.
4. Keep `checksum()` for optional validation, but rewire it to the new helper.

## Tests
- Add unit tests for checksum correctness:
  - Known vectors for even/odd payload lengths.
  - Verify `checksum(packet) == 0` for packets built by
    `build_echo_request`/`build_echo_reply`.
- Add tests that compare the old and new paths on random payloads (if the old
  implementation is preserved under a test-only helper).
- Avoid any raw socket usage in tests.

## Notes
- This change only affects ICMP send performance; receive validation remains
  optional and should keep its current defaults.
- If Option C is explored later, document socket behavior and privilege
  implications in `doc/ICMP_TRANSPORT.md`.
