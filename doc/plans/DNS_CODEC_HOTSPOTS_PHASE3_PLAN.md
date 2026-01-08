# DNS Codec Hotspots Phase 3 Plan

Status: draft

## Goal
- Wire DNS codec caches into client/server initialization and document the
  caching behavior without changing wire format.

## Non-Goals
- Implement cache internals or encode/decode optimizations (phases 1 and 2).
- Change protocol formats or transport semantics.
- Add new base32 optimizations beyond phase 2.

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- doc/architecture/DNS_TRANSPORT.md

## Design Notes
- Preserve output exactly; caching is a pure optimization.
- Initialize caches after config normalization to avoid duplicating validation.
- Keep cache use thread-safe without locks by relying on atomic dict updates
  and read-only cached objects.

## Plan
1. Add a cache warmup helper in the DNS codec.
   - Expose a small function that primes suffix caches using
     `(base_domain, cname_suffix, label_max_len)` after normalization.
2. Wire cache warmup into `DnsClient` and `DnsServer`.
   - Call the warmup helper after config normalization in both client and
     server initialization.
   - Ensure optional `cname_suffix` is handled consistently with existing
     config defaults.
3. Document caching behavior.
   - Update `doc/architecture/DNS_TRANSPORT.md` to describe cache use, bounds,
     and that wire format is unchanged.

## Validation
- `python3 -m py_compile sfb/transport/dns/codec.py`
- `python3 -m py_compile sfb/transport/dns/dns_client.py`
- `python3 -m py_compile sfb/transport/dns/dns_server.py`
