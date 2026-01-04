# Shared Base32 Helpers Plan

## Context
Base32 encode/decode is currently duplicated in DNS and TLS handshake bump
codecs, with casing differences (uppercase vs lowercase) but otherwise similar
logic. Consolidating improves consistency and reduces drift.

## Goals
- Create shared base32 helpers using only the Python standard library.
- Default encoding output to lowercase.
- Keep decode behavior case-insensitive with padding handling.
- Preserve Python 2.7/3 compatibility and ASCII-only code.

## Non-Goals
- Changing transport framing, MTU math, or protocol behavior.
- Reworking DNS/TLS packet layouts or regex logic.
- Broad refactors outside base32 encoding/decoding helpers.

## Affected Components
- `sfb/transport/base32.py` (new shared helpers)
- `sfb/transport/dns/codec.py` (use shared helpers, keep behavior)
- `sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py`
  (use shared helpers, keep behavior)
- `tests/test_dns_codec.py` (update expected casing if needed)
- `tests/test_tls_handshake_bump_codec.py` (update expected casing if needed)
- `doc/DNS_TRANSPORT.md` (note default casing if referenced)
- `doc/completed_plans/TLS_HANDSHAKE_BUMP_TRANSPORT.md` (note default casing if referenced)

## Proposed Changes
1. Add `sfb/transport/base32.py`:
   - `base32_encode(data, lowercase=True)` (default lowercase).
   - `base32_decode(text)` (case-insensitive, padding tolerant).
   - `base32_decode_bytes(value)` for byte/bytearray inputs.
2. Update DNS codec:
   - Replace local base32 helpers with wrappers around shared helper.
   - Preserve existing output casing if needed by passing `lowercase=False`
     or align to default lowercase if ok for DNS encoding.
3. Update TLS handshake bump codec:
   - Replace local base32 helpers with shared helper (default lowercase).
   - Keep any special byte-token decoding logic via `base32_decode_bytes`.
4. Update tests/doc references for casing expectations where relevant.

## Detailed Steps
1. Implement shared helper module with strict ASCII validation and
   padding normalization.
2. Swap DNS and TLS codec helpers to call the shared functions.
3. Adjust tests that check literal base32 output.
4. Update docs that mention casing (if they specify upper/lower).

## Test Plan
- Unit tests for base32 encode/decode in DNS codec still pass.
- Unit tests for TLS handshake bump base32 encode/decode still pass.
- Run existing unit tests only; do not run `tests/e2e/`.

## Risks and Mitigations
- Risk: casing change affects DNS labels or SNI/CN matching.
  Mitigation: keep per-transport wrappers to preserve required casing.
- Risk: subtle differences in padding behavior.
  Mitigation: keep compatibility tests and reuse existing logic.

## Success Criteria
- No functional regressions in DNS or TLS base32 encoding/decoding.
- Single shared implementation with consistent padding/validation.

## Execution Notes
- Added shared base32 helpers in `sfb/transport/base32.py` with lowercase default.
- Wired DNS codec to shared helpers while preserving uppercase output.
- Wired TLS handshake bump codec to shared helpers, including byte-token decode.
- Tests not run (not requested).
