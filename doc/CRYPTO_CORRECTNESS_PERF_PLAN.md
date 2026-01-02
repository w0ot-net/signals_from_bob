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
- Protocol compatibility is not required; this is a breaking wire-format
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
- Breaking wire-format change; older peers are not supported.

### Option C: Packet-scoped crypto with clear nonce prefix
- Prepend a fixed-size nonce in cleartext, encrypt the entire packet body.
- Keeps the header encrypted, but adds overhead and requires MTU adjustments
  and decoding changes.

## Recommendation
Implement Option B. It fixes correctness with minimal wire-format overhead and
keeps the MTU negotiation unchanged. Document the header visibility tradeoff.
This is a protocol-breaking change; ship with both sides updated and no
compatibility shims.

## Implementation Sketch
1. Define a packet-oriented crypto API in `sfb/crypto.py`:
   - `encrypt(data, seq=None, direction=None)`
   - `decrypt(data, seq=None, direction=None)`
   - Plain/XOR ignore `seq` and `direction`.
   - RC4 requires `seq` and `direction` (raise `ValueError` when missing) and
     uses them for key derivation.
2. Implement RC4 as stateless per packet:
   - Derive a per-packet key by concatenating the base key with a nonce:
     `key = base_key + struct.pack('>HB', seq, direction)`.
   - Validate `seq` is 0-65535 and `direction` is 0 or 1.
   - Reinitialize RC4 for each encrypt/decrypt call with the derived key.
3. Cache encrypted bodies for retransmits:
   - Extend the send window to store the encoded body (segments only) and the
     encrypted body for each sequence number.
   - Preserve current metadata needed by stats (segments or a has-data flag)
     so `data_acked_count` keeps its meaning.
   - Update any tests/call sites that rely on the current send_window payload
     shape (see `tests/test_reliability.py` and `tests/test_tunnel.py`).
   - On retransmit, reuse the cached encrypted body to avoid repeating KSA/PRGA
     work and keep retransmit ciphertext deterministic.
4. Update tunnel encryption flow in `sfb/tunnel/base_tunnel.py`:
   - Encode header and body separately (avoid full-packet encode + slice).
   - Send `header + encrypt(body, seq, direction)`; use cached encrypted body
     for retransmits when available.
   - On receive, parse header first (PacketHeader.decode) to get `seq`, then
     decrypt the body and decode segments (Segment.decode_all) before
     assembling a Packet from header + segments.
   - Preserve max packet size enforcement by checking `len(data) <= max_size`
     before decoding the header/body.
   - Preserve control-segment logging (call the protocol helper after segment
     decode).
5. Split direction deterministically:
   - Use `direction = 0` for Alice-to-Bob packets and `1` for Bob-to-Alice.
   - Derive direction from `self._is_initiator` and send/receive context.
6. Optimize XOR/RC4 hot loops:
   - Use a single `bytearray` buffer and mutate in place.
   - Replace modulo per byte with an index that wraps at `key_len`.
7. Update docs to reflect header visibility, packet-scoped crypto, and the
   keystream reuse risk when seq wraps with a static PSK:
   - `doc/TUNNEL.md` encryption section.
   - `doc/PROTOCOL.md` packet encryption overview.
   - `doc/ARCHITECTURE.md` crypto section if it references full-packet encryption.
   - `doc/RELIABILITY.md` receive path encryption wording.
   - `doc/ICMP_TRANSPORT.md` payload description.
   - `doc/TRANSPORTS.md` transport payload description.
   - `sfb/crypto.py` module docstring.
8. Update tests:
   - `tests/test_crypto.py`: add deterministic encryption test
     (same `seq` yields same ciphertext), retransmit decrypt test, and
     direction-separation test; update RC4 usage to pass `seq`/`direction`.
   - `tests/test_tunnel.py`: avoid `Packet.decode()` on encrypted wire bytes;
     decode via the tunnel path or header+segments helpers.
   - `tests/test_reliability.py`: adjust SendWindow tests if the stored payload
     or method signatures change.
   - Add a focused tunnel test to ensure a retransmitted packet decrypts
     correctly (unit-level; no E2E).

## Tests
- `python3 -m unittest tests/test_crypto.py`
- `python3 -m unittest tests/test_tunnel.py` (if targeted tests are added)

## Notes
- If Option C is chosen instead, adjust MTU computations and `max_packet_size`
  to account for nonce overhead, and update tests accordingly.
- Keystream reuse risk: per-packet RC4 derived only from `seq` and `direction`
  will reuse keystreams across sessions (fixed ISN) and after seq wrap unless a
  per-session salt or randomized ISN is introduced. Accepted for now; do not
  address in this change.
- This is a breaking wire-format change; do not add protocol negotiation or
  backward compatibility in this change.
- When executing this plan, add execution notes and move the plan to
  `doc/completed_plans/` per project rules.
