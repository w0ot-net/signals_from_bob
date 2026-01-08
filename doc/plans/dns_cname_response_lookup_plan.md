# DNS CNAME Response Lookup Plan

## Summary
Eliminate the binary search in `_max_cname_payload_for_response` by replacing
it with a precomputed lookup table derived from the same wire-length rules.
If the resulting logic is clean, centralize the table construction so both
client and server can reuse it without duplicate calculations.

## Goals
- Remove per-call binary search in `_max_cname_payload_for_response`.
- Preserve exact payload cap results for all current inputs.
- Keep the central logic minimal and easy to audit.
- Optionally share the lookup logic between DNS client and server if it
  reduces duplication without adding complexity.

## Affected Components
- `sfb/transport/dns/dns_codec.py` (lookup helper + removal of binary search)
- `sfb/transport/dns/dns_client.py` (reuse lookup if centralized)
- `sfb/transport/dns/dns_server.py` (reuse lookup if centralized)

## Plan
1. Design a lookup table:
   - For fixed `cname_suffix`, `label_max_len`, and `max_packet_size`, compute
     a list where index `payload_len` yields `rdata_len` or `total_len`.
   - Derive the largest `payload_len` where `fixed_len + rdata_len <= max_packet_size`.
   - Keep computation bounded by the current `calc_response_mtu` upper bound.
2. Implement a helper in `sfb/transport/dns/dns_codec.py`:
   - Example: `build_cname_rdata_len_table(cname_suffix, label_max_len, max_packet_size)`
     returning a list or tuple of `rdata_len` per payload length.
   - Example: `max_cname_payload_from_table(fixed_len, rdata_lens, max_packet_size)`
     returning the cap in O(n) or O(1) after precomputation.
   - Replace `_max_cname_payload_for_response` to use the table (no binary search).
3. Evaluate shared usage:
   - If the client already builds a response-cap table, consider reusing the
     same table structure for server caps to avoid duplication.
   - Only centralize if it reduces total code and avoids awkward coupling.
4. Keep fallback minimal:
   - If centralizing adds complexity, keep server/client tables separate but
     both built with the same codec helper.
5. Validate equivalence:
   - Compare old/new cap outputs for representative sizes in a focused
     validation script (no tests/ changes) and document in execution notes.

## Success Criteria
- `_max_cname_payload_for_response` no longer uses binary search.
- Server/client capacity calculations remain unchanged.
- Shared logic is centralized only if it is clearly simpler than keeping
  separate tables.
