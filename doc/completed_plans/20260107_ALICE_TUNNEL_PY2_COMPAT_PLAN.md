# Alice Tunnel Python 2 Compatibility Plan

Status: completed

## Goal
- Ensure `sfb/tunnel/alice_tunnel.py` is fully compatible with Python 2.7 and
  Python 3 while preserving current behavior.

## Non-Goals
- Change tunnel semantics (retransmit logic, pacing, keepalive behavior).
- Fix Python 2 issues in other tunnel modules (handled separately).
- Add non-stdlib dependencies or platform-specific behavior.

## Affected Components
- sfb/tunnel/alice_tunnel.py

## Plan
1. Audit Python 3-only syntax.
   - Confirm no f-strings, dict unpacking (`{**a}`), keyword-only args,
     `super()` without args, or type annotations.
2. Validate bytes/text handling.
   - Check any JSON/control-message boundaries for implicit unicode/bytes
     mixing and add `compat` helpers (`to_bytes`, `to_native_str`) if needed.
3. Validate numeric semantics.
   - Ensure divisions that expect floats are explicit to avoid Python 2 integer
     division surprises (use `float(...)` where appropriate).
4. Apply minimal edits.
   - Keep code ASCII-only and stdlib-only; update any call sites if signatures
     change.

## Validation
- `python3 -m py_compile sfb/tunnel/alice_tunnel.py`
- Optional: `python2.7 -m py_compile sfb/tunnel/alice_tunnel.py`

## Execution Notes
- Reviewed `sfb/tunnel/alice_tunnel.py` for Python 2/3 compatibility; no
  Python 3-only syntax or bytes/str hazards found, so no code changes needed.
