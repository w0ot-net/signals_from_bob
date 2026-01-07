# RC4 Throughput and CPU Plan

Status: abandoned

## Goal
- Reduce RC4 CPU usage and its impact on throughput while preserving packet-
  scoped encryption (seq + direction) and Python 2.7/3 compatibility.

## Non-Goals
- Change transport asymmetry, MTU negotiation, or reliability behavior.
- Add non-standard-library dependencies.
- Alter the RC4 wire format in a way that breaks compatibility without an
  explicit new mode name.

## Affected Components
- sfb/crypto.py
- sfb/compat.py (only if byte/memoryview helpers need tuning)
- doc/architecture/PROTOCOL.md
- doc/architecture/TUNNEL.md
- doc/architecture/ARCHITECTURE.md

## Plan
1. Establish a baseline for RC4 cost on typical packet sizes.
   - Measure per-packet overhead (KSA) vs per-byte overhead (PRGA) with a
     small local benchmark using python3.
   - Record sizes that match negotiated payload MTUs so improvements are
     representative of real traffic.

2. Cut per-call overhead in `_rc4_crypt`.
   - Add an early return for empty data to skip the KSA entirely.
   - Precompute the 256-byte key stream for KSA (`key[i % key_len]`) to remove
     modulus work in the loop.
   - Keep hot variables local (s, key_stream, out, i, j) and use bytearray for
     `s` and `out` to reduce Python overhead.

3. Reduce allocation churn in RC4 key handling.
   - Store RC4 base keys as immutable bytes to avoid repeated bytearray copies.
   - Build derived keys with bytes concatenation and avoid extra conversions
     on the fast path.
   - Keep `_derive_rc4_key` behavior identical (seq + direction) so ciphertext
     remains stable across retransmits.

4. Validate correctness and compatibility.
   - Ensure encrypt/decrypt round-trip and deterministic output for a fixed
     (psk, seq, direction, payload).
   - Confirm no changes to packet layout or crypto API expectations.

5. Document the RC4 performance tradeoffs.
   - Update protocol and tunnel docs to mention RC4 CPU costs and recommend
     alternatives when throughput is the priority.

6. Decision gate if RC4 is still too slow.
   - If measured gains are insufficient, add a new stream cipher mode based on
     standard-library hashing (C-backed) rather than silently changing RC4.
   - Document and expose the new mode via config/CLI, leaving RC4 intact for
     compatibility.

## Abandonment notes
- 2026-01-07: Abandoned per request; no implementation work recorded.
