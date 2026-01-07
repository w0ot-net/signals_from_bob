# SHA256 Stream Mode Plan

## Goal
- Add a new `sha256` stream cipher mode that reduces CPU usage versus RC4
  while keeping packet-scoped encryption (seq + direction) and Python 2.7/3
  compatibility.

## Non-Goals
- Replace or remove RC4.
- Add message authentication or integrity checks.
- Change packet layout, MTU negotiation, or transport asymmetry.

## Affected Components
- sfb/crypto.py
- sfb/config.py
- sfb/cli.py
- doc/architecture/PROTOCOL.md
- doc/architecture/TUNNEL.md
- doc/architecture/ARCHITECTURE.md

## Plan
1. Specify the keystream construction and inputs.
   - Nonce: `struct.pack('>HB', seq, direction)` (same scoping as RC4).
   - Packet key: `HMAC-SHA256(psk, b'sfb-sha256' + nonce)`.
   - Keystream blocks: `SHA256(packet_key + counter_be32)` for counter = 0..N.
   - Ciphertext: XOR payload with the generated keystream.

2. Implement the SHA256 cipher in `sfb/crypto.py`.
   - Add a `SHA256` class with `encrypt/decrypt` and a helper to generate the
     keystream, returning empty output for empty input.
   - Keep all byte handling Python 2/3 safe via `require_bytes_like` and
     `to_bytes`.
   - Add `sha256` to `CIPHER_MODES` and update the module docstring.

3. Wire the mode into configuration and CLI.
   - Allow `crypto_mode="sha256"` in config validation and comments.
   - Add `--sha256` CLI flag (mutually exclusive with xor/rc4).
   - Update `create_crypto` to instantiate the new class and log mode.

4. Update docs and architecture references.
   - Expand cipher lists in PROTOCOL/TUNNEL/ARCHITECTURE docs to include
     `sha256` and describe the per-packet keystream derivation.
   - Note that it is a stream cipher without integrity, same as RC4/XOR.

5. Compatibility check.
   - Ensure retransmit determinism remains (seq + direction fixed keystream).
   - Confirm no behavior changes to existing modes.

## Execution Notes
- Implemented SHA256 stream cipher with per-packet HMAC key and counter-mode
  keystream in sfb/crypto.py.
- Wired sha256 mode into config validation, CLI flags, and cipher selection.
- Updated protocol, tunnel, and architecture docs to include sha256.
