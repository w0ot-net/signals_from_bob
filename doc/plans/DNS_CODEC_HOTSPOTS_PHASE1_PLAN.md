# DNS Codec Hotspots Phase 1 Plan

Status: draft

## Goal
- Land low-risk caching for suffix encodings and tighten the
  `encode_query_name` hot path without changing wire format.

## Non-Goals
- Optimize `decode_name`.
- Add base32 caching.
- Wire caches into client/server initialization.
- Update transport documentation.

## Affected Components
- sfb/transport/dns/codec.py

## Design Notes
- Preserve output exactly; optimizations must be behavior-neutral.
- Cache immutable suffix encodings keyed by `(domain, label_max_len)`.
- Store encoded bytes plus precomputed label/total lengths for fast checks.
- Keep cache bounded (simple max-size eviction) to avoid unbounded growth.

## Plan
1. Add a module-level cache for suffix encodings.
   - Store base_domain and cname_suffix encodings with precomputed lengths.
   - Implement a tiny bounded dict with eviction on insert overflow.
2. Optimize `encode_query_name` to use cached suffix encodings.
   - Reuse cached suffix bytes instead of re-encoding labels per call.
   - Replace repeated `_validate_name_length` calls with cached total length
     plus incremental label lengths for new labels.
   - Minimize bytes/text conversions by accepting bytes inputs when provided.

## Validation
- `python3 -m py_compile sfb/transport/dns/codec.py`
- Manual sanity: encode a fixed payload/domain pair and confirm output is
  unchanged.
