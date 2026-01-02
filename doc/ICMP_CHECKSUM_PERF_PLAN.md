# ICMP Checksum Performance Plan

## Goal
Eliminate per-part array allocations and per-part summing for outbound ICMP
checksums so higher packet rates are possible.

## Issue
- `_checksum_parts` allocates `array('H')` per part and calls `sum()` per part,
  which caps PPS under load.
- Affects `sfb/transport/icmp/icmp_packet.py:44` and
  `sfb/transport/icmp/icmp_packet.py:75`.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Linux and Windows (ICMP transport remains Linux-only).
- Preserve ICMP Echo Request/Reply wire format.

## Plan
1. Add a buffer-based checksum helper in `sfb/transport/icmp/icmp_packet.py`
   that accepts a contiguous buffer without forcing an extra full-packet copy:
   - Accept bytes-like inputs (`bytes`, `bytearray`, `memoryview`) and only
     normalize when needed (avoid bytearray->bytes on Python 2 when possible).
   - Handle odd-length payload by treating the last byte as high order and
     trimming to an even-length prefix before casting/loading.
   - Python 3 big-endian fast path: `memoryview.cast('H')` to avoid any
     allocation (native endianness only). Require C-contiguous, even-length
     input; fall back to the array path if `cast` fails.
   - Python 2 and all little-endian hosts: a single `array('H')` populated via
     `array_frombytes`, followed by `byteswap()` when host endianness is
     little.
   - Note: Python 2 may still need a bytes or buffer-compatible view for
     `array_frombytes`; document if a bytearray copy is unavoidable.
2. Update `build_echo_packet()` to construct the full packet once, while
   avoiding extra full-packet copies and returning a view where possible:
   - Allocate `bytearray` for header + payload.
   - `struct.pack_into` header with checksum set to zero.
   - Copy payload into the buffer.
   - Compute checksum on the buffer and patch it in place with `pack_into`.
   - Python 2: convert to bytes once, reuse for checksum and return bytes.
   - Python 3: return a `memoryview` of the packet buffer to avoid a final
     bytes copy; update any concatenation call sites to coerce to bytes first.
3. Update callers to accept bytes-like packets, not just `bytes`:
   - Use `to_bytes()` only when a real `bytes` object is required.
   - Confirm `socket.sendto` paths accept the returned buffer object.
   - Update tests that concatenate packets (for example IPv4 header + ICMP)
     to call `to_bytes()` or `bytes()` on the ICMP packet first.
4. Remove or inline `_checksum_parts`; keep `checksum()` but rewire it to the
   new helper for validation.
5. Add unit tests for checksum correctness:
   - Known full-packet vectors (header + payload) for even and odd payload
     lengths with expected checksum values.
   - `checksum(packet) == 0` for packets built by
     `build_echo_request`/`build_echo_reply`.

## Notes
- `doc/ICMP_CHECKSUM_PLAN.md` has more background and alternative options.

## Acceptance Criteria
- No per-part `array('H')` allocations on the outbound checksum path.
- No extra full-packet copies beyond what is needed for the returned value
  (memoryview on Python 3, bytes on Python 2).
- Packet bytes are identical to the current output.
- Unit tests pass (no E2E tests run here).
