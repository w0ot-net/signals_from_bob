# DNS Domain Python 2 Compatibility Plan

Status: completed

## Goal
- Fix Python 2 DNS transport initialization when `dns_base_domain` is a byte
  string (e.g., CLI input) by normalizing it to text consistently in
  `sfb/transport/dns/codec.py`.

## Non-Goals
- Change DNS encoding semantics or allow non-ASCII domain names.
- Modify tunnel behavior outside DNS domain normalization.

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/mtu_limits.py (call site validation path)
- sfb/cli.py (optional: ensure CLI domain is text)

## Plan
1. Make DNS domain normalization accept bytes.
   - Update `_normalize_domain` to accept `bytes`/`str` input and decode ASCII
     to `text_type` when needed.
   - Keep the existing text-type check after decoding and preserve the
     trailing-dot trimming behavior.
   - Raise a clear `TypeError`/`ValueError` when non-ASCII bytes are supplied.
2. Optional: normalize CLI domain input.
   - In `sfb/cli.py`, coerce `args.domain` to text using ASCII decode on Python 2
     before passing into `Config(...)` to keep config state consistent.
3. Keep behavior unchanged elsewhere.
   - Do not change DNS label validation, MTU sizing logic, or transport
     semantics.

## Validation
- `python3 -m py_compile sfb/transport/dns/codec.py`
- Optional: `python2.7 -m py_compile sfb/transport/dns/codec.py`
- Manual sanity: `python2 -m sfb.cli --role alice --domain ebaysso.com --max-in-flight 256`

## Execution Notes
- Updated `_normalize_domain` in `sfb/transport/dns/codec.py` to accept byte
  strings by ASCII-decoding them before validation, preserving existing
  behavior for text input.
