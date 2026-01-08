# DNS Codec Hotspots Plan

Status: draft

## Goal
- Reduce CPU overhead in DNS encode/decode hot paths without changing wire
  format or transport semantics.

## Non-Goals
- Change protocol formats, label splitting rules, or base32 alphabet.
- Introduce non-stdlib dependencies.

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/base32.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes
- Preserve current output exactly; optimizations must be behavior-neutral.
- Prefer caching immutable suffix encodings (base_domain, cname_suffix) keyed
  by label_max_len and domain string to avoid repeated encoding work.
- Keep the critical paths allocation-light and avoid repeated bytes/text
  conversions when inputs are already bytes.
- Maintain strict length validation for externally provided names; use
  precomputed label lengths for cached suffixes to avoid repeated checks.

## Phases
- Phase 1: `doc/plans/DNS_CODEC_HOTSPOTS_PHASE1_PLAN.md`
  - Suffix cache + `encode_query_name` hot path.
- Phase 2: `doc/plans/DNS_CODEC_HOTSPOTS_PHASE2_PLAN.md`
  - `decode_name` optimization + base32 hot paths.
- Phase 3: `doc/plans/DNS_CODEC_HOTSPOTS_PHASE3_PLAN.md`
  - Client/server wiring + documentation updates.

## Plan
This plan is split into phases for execution; see phase docs above.

1. Add a DNS codec cache for suffix encodings.
   - Implement a small module-level cache in `sfb/transport/dns/codec.py` for:
     - base_domain labels (as encoded wire fragments + total length).
     - cname_suffix labels (encoded wire fragments + total length).
   - Key by `(domain, label_max_len)` and store both the encoded name bytes and
     precomputed label/total lengths for fast length checks.
   - Keep cache bounded by size (simple LRU or max-size eviction) to avoid
     unbounded growth.
2. Optimize `encode_query_name`.
   - Reuse cached base_domain encoding instead of re-encoding labels per call.
   - Avoid repeated `_validate_name_length` by using cached total-length data
     and incremental length checks as labels are appended.
   - Minimize bytes/text conversions by accepting bytes inputs where possible
     and only converting once.
3. Optimize `decode_name`.
   - Keep fast-path parsing for labels and avoid redundant validation when
     decoding query names with known suffixes.
   - Add a helper that returns both the decoded name and raw label slices to
     reduce intermediate allocations when decoding tunnel payloads.
4. Optimize base32 usage.
   - Add a small cache for base32 encodings of the static suffix portions and
     any constant overhead prefixes (nonce label formatting).
   - Reduce repeated calls to stdlib `_b32encode` by deferring base32 work to
     only the variable payload portion.
5. Wire the caches into client and server.
   - Initialize codec caches with base_domain and cname_suffix in
     `DnsClient` and `DnsServer` after config normalization.
   - Ensure cache use is thread-safe without locks by relying on atomic dict
     assignment and read-only cached objects.
6. Document behavior.
   - Update `doc/architecture/DNS_TRANSPORT.md` to mention cache use and note
     that output format is unchanged.

## Validation
- `python3 -m py_compile sfb/transport/dns/codec.py sfb/transport/base32.py`
- Manual sanity: run a DNS client/server pair and confirm queries/responses are
  unchanged for a fixed payload and base domain.
