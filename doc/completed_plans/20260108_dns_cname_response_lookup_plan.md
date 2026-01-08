# DNS CNAME Response Lookup Plan

## Summary
Replace the binary search in `_max_cname_payload_for_response` with a cached
lookup table that maps available RDATA bytes to the maximum payload length.
The table is built once per `(cname_suffix, label_max_len, max_packet_size)`
in `dns_codec`, so both client and server reuse the same centralized logic.

## Goals
- Remove per-call binary search from `_max_cname_payload_for_response`.
- Keep exact payload cap results for all current inputs.
- Centralize the lookup in `dns_codec` so client and server share it without
  extra wiring.
- Reduce code complexity by deleting the now-unused binary search helper.

## Affected Components
- `sfb/transport/dns/dns_codec.py`

## Plan
1. Add a module-level cache in `sfb/transport/dns/dns_codec.py` keyed by
   `(cname_suffix, label_max_len, max_packet_size)` and a helper
   `_get_cname_payload_lookup(...)` that returns a lookup list. The helper
   assumes normalized inputs (same as `_max_cname_payload_for_response`).
2. In `_get_cname_payload_lookup(...)`, build the lookup list by:
   - Computing `upper` with `calc_response_mtu(QTYPE_CNAME, ...)`.
   - Building `rdata_lens[payload_len]` for `payload_len` in `0..upper` using
     `encode_cname_target` + `encode_name`; on `ValueError` store `None` to
     mark an invalid payload length (do not treat invalid entries as a fit).
   - Building `payload_for_available[available]` for `available` in
     `0..max_packet_size` (inclusive) by scanning `rdata_lens` once and
     tracking the largest payload that fits each `available` byte count.
     Only update the best payload when `rdata_len` is not `None` and
     `rdata_len <= available`. Leave the default best at 0 so a no-fit case
     still returns 0, matching the current binary-search behavior even when
     payload_len=0 does not fit.
3. Update `_max_cname_payload_for_response` to use the lookup:
   - Return 0 when `fixed_len >= max_packet_size`.
   - Otherwise compute `available = max_packet_size - fixed_len` and return
     `payload_for_available[available]` (the lookup list length must be
     `max_packet_size + 1` to make this index safe).
4. Validate equivalence by running a small local `python3` script that compares the old
   and new outputs for a representative set of `(qname_wire_len, edns_size,
   cname_suffix, label_max_len)` combinations; do this before removing the
   helper or embed the old binary-search logic in the script, and record the
   results in the execution notes when the plan is executed.
5. Remove `_binary_search_max` from `dns_codec.py` since it is no longer used.

## Success Criteria
- `_max_cname_payload_for_response` no longer uses binary search.
- `calc_cname_response_payload_cap` outputs remain unchanged.
- The lookup cache lives in `dns_codec` and is reused by both client and
  server without additional wiring.

## Execution Notes
- 20260108: Implemented the lookup cache in `sfb/transport/dns/dns_codec.py`,
  removed the binary search helper, and verified equivalence with a local
  `python3` script using embedded old binary-search logic
  (comparisons=1440, mismatches=0).
