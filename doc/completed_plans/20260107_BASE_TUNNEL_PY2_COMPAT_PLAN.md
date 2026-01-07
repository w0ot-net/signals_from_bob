# Base Tunnel Python 2 Compatibility Plan

Status: completed

## Goal
- Ensure `sfb/tunnel/base_tunnel.py` remains fully compatible with Python 2.7
  and Python 3 while preserving current tunnel behavior.

## Non-Goals
- Change tunnel semantics (asymmetric MTU negotiation, retransmit/timeout
  logic, keepalive suppression).
- Add non-stdlib dependencies or platform-specific behavior.

## Affected Components
- sfb/tunnel/base_tunnel.py

## Plan
1. Audit Python 3-only syntax and behavior.
   - Confirm no f-strings, type annotations, keyword-only args, or `super()`
     without arguments.
   - Verify dict view usage is not relied upon (no indexing of `keys()`/`items()`).
2. Validate numeric semantics across runtimes.
   - Review divisions used in timing/ratios (RTO, pacing, EWMA) and ensure
     float results are explicit when needed (use `float(...)` casts).
   - Keep integer boundary checks using `integer_types` to preserve Py2 `long`.
3. Verify bytes/text boundaries.
   - Ensure JSON/control message handling is strict about bytes vs text.
   - Use `compat` helpers (`to_bytes`, `to_native_str`) where needed to avoid
     implicit unicode/bytes mixing in Py2.
4. Apply minimal edits to align with Python 2.
   - Keep code ASCII-only and stdlib-only.
   - Update any affected call sites if signatures or return types change.

## Validation
- `python3 -m py_compile sfb/tunnel/base_tunnel.py`
- Optional: `python2.7 -m py_compile sfb/tunnel/base_tunnel.py`

## Execution Notes
- Reviewed `sfb/tunnel/base_tunnel.py` for Python 2/3 compatibility; no
  Python 3-only syntax or bytes/str hazards found, so no code changes needed.
