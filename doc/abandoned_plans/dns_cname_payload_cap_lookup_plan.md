# DNS CNAME Payload Cap Lookup Plan

## Summary
Replace the nested binary search in `calc_cname_payload_cap` with a
precomputed lookup derived from the response-cap table built at DNS client
startup. This keeps behavior unchanged while avoiding repeated binary search
work and consolidating capacity logic in one place.

## Goals
- Avoid nested binary searches when deriving the symmetric CNAME payload cap.
- Compute the cap once per config at startup using existing response-cap data.
- Keep results identical to the current `calc_cname_payload_cap` logic.
- Keep the implementation readable and easy to audit.

## Affected Components
- `sfb/transport/dns/dns_codec.py` (new helper and optional fast path)
- `sfb/transport/dns/dns_client.py` (reuse response-cap table to compute cap)

## Plan
1. Add a helper in `sfb/transport/dns/dns_codec.py` to compute the symmetric
   cap from a response-cap lookup table (for example,
   `calc_cname_payload_cap_from_caps(response_caps)`), and update
   `calc_cname_payload_cap` to accept an optional table so it can skip the
   binary search when the table is provided.
2. In `DnsClient._init_response_caps`, after building `response_caps`, compute
   the symmetric cap with the new helper and store it on the instance (for
   example `_cname_payload_cap`). Optionally add a debug log with the value.
3. Keep the current binary search as a fallback in `calc_cname_payload_cap`
   when no table is provided so external callers retain the same API.

## Success Criteria
- `calc_cname_payload_cap` can return results without running a binary search
  when a response-cap table is available.
- DNS client startup computes and caches the symmetric cap once per config.
- No protocol or MTU behavior changes from current results.
