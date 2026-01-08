# DNS Codec Hotspots Phase 2 Plan

Status: completed

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

## Execution Notes
- Added a decode helper that returns both the name string and raw label slices,
  and tightened `decode_name` length validation during parsing.
- Reused cached suffix labels in query/CNAME decode paths to avoid revalidating
  known suffix portions on every call.
- Added a bounded cache for nonce label base32 encoding to reuse constant
  overhead prefixes.
- Tests not run (per instructions).
