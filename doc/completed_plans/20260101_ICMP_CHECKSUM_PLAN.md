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
  - For correctness on little-endian hosts, byteswap the 16-bit words before
    summing (since the wire format is big-endian).
  - Do not mutate or extend the packet buffer for odd lengths; handle the
    trailing byte by adding `last_byte << 8`.
  - Python 3: use `memoryview.cast('H')` only on big-endian hosts and even
    lengths; use `array('H')` otherwise to avoid cast errors and byteswap on
    little-endian.
  - Python 2: normalize to a bytes object and use `array('H').fromstring(...)`
    (single allocation).
- Remove `_checksum_parts` entirely and update any call sites to use the new
  buffer-based checksum.

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
   - Handle odd-length buffers by accounting for the last byte without
     modifying the packet buffer.
   - If host byte order is little-endian, byteswap the 16-bit words before
     summing so the result matches the network byte order.
   - Python 3 fast path with `memoryview.cast('H')` on big-endian hosts when
     the length is even.
   - Python 2 fallback with `array('H')` and `fromstring`, after normalizing
     to bytes.
2. Update `build_echo_packet()`:
   - Allocate a `bytearray` sized for header + payload.
   - `struct.pack_into('>BBHHH', buf, 0, icmp_type, ICMP_CODE, 0, ident, seq)`.
   - Copy payload into the buffer.
   - Compute checksum on the buffer and patch it with `pack_into`.
   - Return `bytes(buf)` (or `str` in Python 2 via existing compat helper).
3. Remove `_checksum_parts` and update any call sites to pass a buffer.
4. Keep `checksum()` for optional validation, but rewire it to the new helper.

## Tests
- Add unit tests for checksum correctness:
  - Known vectors for even/odd payload lengths.
  - Coverage for little-endian hosts (byteswap path).
  - Verify `checksum(packet) == 0` for packets built by
    `build_echo_request`/`build_echo_reply`.
- If needed, add a small reference checksum helper in tests to compare random
  payloads without keeping the old implementation in production code.
- Avoid any raw socket usage in tests.

## Notes
- This change only affects ICMP send performance; receive validation remains
  optional and should keep its current defaults.
- If Option C is explored later, document socket behavior and privilege
  implications in `doc/ICMP_TRANSPORT.md`.

## Execution Notes
- Implemented `_checksum_buffer` with a contiguous buffer view and a
  big-endian `memoryview.cast('H')` fast path; little-endian hosts use a
  single `array('H')` with byteswap.
- Reworked `build_echo_packet()` to build a single `bytearray`, patch the
  checksum in place, and return bytes on Python 2 or a `memoryview` on
  Python 3.
- Added unit tests for known even/odd checksum vectors and zero checksum
  validation for built packets.
