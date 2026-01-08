# DNS Codec Hotspots Phase 2 Plan

Status: draft

## Goal
- Reduce CPU overhead in DNS `decode_name` and base32 usage without changing
  wire format or transport semantics.

## Non-Goals
- Add suffix encoding cache for `encode_query_name` (phase 1).
- Wire caches into client/server initialization.
- Update transport documentation.

## Affected Components
- sfb/transport/dns/codec.py

## Design Notes
- Preserve output exactly; optimizations must be behavior-neutral.
- Keep decode fast paths allocation-light and avoid redundant validation when
  bounds are already checked.
- Cache only static or small, bounded base32 encodings to avoid unbounded
  memory growth.

## Plan
1. Optimize `decode_name`.
   - Add a helper that returns the decoded name plus raw label slices to reduce
     intermediate allocations when decoding tunnel payloads.
   - Skip repeated validation for known suffix portions once label lengths are
     verified.
2. Optimize base32 usage in DNS codec.
   - Cache base32 encodings for small, repeatable fragments (nonce label
     formatting or constant suffix portions).
   - Ensure base32 encoding is only applied to the variable payload segment,
     not to constant overhead that can be reused.

## Validation
- `python3 -m py_compile sfb/transport/dns/codec.py`
- Manual sanity: round-trip encode/decode with a fixed payload and confirm
  wire format is unchanged.
