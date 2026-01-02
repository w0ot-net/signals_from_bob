# Crypto Correctness and Performance Plan

## Goal
Fix RC4 correctness with retransmits and bidirectional traffic, and reduce
per-packet overhead in XOR/RC4 while keeping Python 2.7/3 compatibility and
standard-library-only constraints.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux.
- Avoid running E2E tests in `tests/e2e/`.
- Breaking changes are acceptable if all call sites are updated in the same
  change.
- Keep code and scripts ASCII-only.

## Current Behavior (Problems)
- RC4 is stateful per instance and the same instance is used for encrypt and
  decrypt in `BaseTunnel`, so send/recv operations interleave the keystream.
- RC4 advances state on each call; retransmits re-encrypt with a different
  keystream, so the peer cannot decrypt and drops packets.
- RC4 and XOR allocate two buffers per call (input copy + output), and XOR
  uses modulo per byte in the hot loop.

## Options

### Option A: Remove RC4 support entirely
- Drop `rc4` from `CIPHER_MODES` and config validation.
- Update docs/tests to only mention `none` and `xor`.
- Simplest change but removes the only "stronger" cipher option.

### Option B: Packet-scoped crypto with clear header (recommended)
- Leave the packet header unencrypted and encrypt only the body (segments).
- Derive a per-packet keystream from `(base_key, seq, direction)` so the same
  packet (same `seq`) encrypts deterministically, enabling retransmits and
  out-of-order reception.
- No extra wire overhead; MTU calculations remain unchanged.
- Requires documentation updates since the header is no longer encrypted.

### Option C: Packet-scoped crypto with clear nonce prefix
- Prepend a fixed-size nonce in cleartext, encrypt the entire packet body.
- Keeps the header encrypted, but adds overhead and requires MTU adjustments
  and decoding changes.

## Recommendation
Implement Option B. It fixes correctness with minimal wire-format overhead and
keeps the MTU negotiation unchanged. Document the header visibility tradeoff.

## Implementation Sketch
1. Define a packet-oriented crypto API in `sfb/crypto.py`:
   - `encrypt(data, seq=None, direction=None)`
   - `decrypt(data, seq=None, direction=None)`
   - Plain/XOR ignore `seq` and `direction`; RC4 uses them.
2. Implement RC4 as stateless per packet:
   - Derive a per-packet key using `hashlib.sha256(base_key + nonce).digest()`
     where `nonce = struct.pack('>HB', seq, direction)`.
   - Reinitialize RC4 for each encrypt/decrypt call with the derived key.
3. Update tunnel encryption flow in `sfb/tunnel/base_tunnel.py`:
   - Encode header and body separately.
   - Send `header + encrypt(body, seq, direction)`.
   - On receive, parse header first to get `seq`, then decrypt the body and
     decode `Packet` from `header + decrypted_body`.
4. Split direction deterministically:
   - Use `direction = 0` for Alice-to-Bob packets and `1` for Bob-to-Alice.
   - Derive direction from `self._is_initiator` and send/receive context.
5. Optimize XOR/RC4 hot loops:
   - Use a single `bytearray` buffer and mutate in place.
   - Replace modulo per byte with an index that wraps at `key_len`.
6. Update docs to reflect header visibility and packet-scoped crypto:
   - `doc/TUNNEL.md` encryption section.
   - `doc/ARCHITECTURE.md` crypto section if it references full-packet encryption.
7. Update tests:
   - `tests/test_crypto.py`: add deterministic encryption test
     (same `seq` yields same ciphertext), retransmit decrypt test, and
     direction-separation test.
   - Add a focused tunnel test to ensure a retransmitted packet decrypts
     correctly (unit-level; no E2E).

## Tests
- `python3 -m unittest tests/test_crypto.py`
- `python3 -m unittest tests/test_tunnel.py` (if targeted tests are added)

## Notes
- If Option C is chosen instead, adjust MTU computations and `max_packet_size`
  to account for nonce overhead, and update tests accordingly.
- When executing this plan, add execution notes and move the plan to
  `doc/completed_plans/` per project rules.
