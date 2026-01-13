# DNS CNAME Compression Plan

Status: draft

## Summary
Enable and require DNS name compression in CNAME responses to reduce response size and
increase effective payload capacity for DNS transport.

## Goals
- Use compression pointers for the CNAME answer name and CNAME target suffix.
- Require compression for all CNAME responses (no uncompressed fallback).
- Update payload cap calculations to account for compressed responses.
- Preserve DNS protocol behavior and client decoding.

## Non-Goals
- Add new configuration options or toggles.
- Change query encoding or record types.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/cli.py`

## Plan
1. Add compression helpers in `sfb/transport/dns/dns_codec.py`.
   - Add a small helper to build a compression pointer (`0xC000 | offset`) and
     validate that `offset <= 0x3FFF`.
   - Add a helper to compute the base-domain pointer offset as an absolute
     message offset (include the 12-byte DNS header and the fact QNAME starts
     at offset 12). Use `qname_wire_len` and `base_domain_wire_len` to derive
     the suffix start (offset = 12 + qname_wire_len - base_domain_wire_len),
     and validate it fits the 14-bit pointer range.
   - Add a helper that builds CNAME RDATA with a compressed base-domain suffix:
     encode data labels + CNAME label, then append a compression pointer to the
     base-domain offset.
2. Update payload cap calculations for compression.
   - Extend `calc_cname_response_payload_cap` to accept `base_domain` and
     compute:
     - answer name length as 2 bytes (compression required).
     - CNAME RDATA length using the compressed suffix helper.
   - If the base-domain pointer offset is invalid, treat the cap as unavailable
     (no uncompressed fallback) to match response encoding behavior.
   - Keep a shared lookup cache for the compressed mode so repeated calls do
     not re-encode payloads.
3. Use compression in `sfb/transport/dns/dns_server.py`.
   - Build the answer name as a pointer to the question name (offset 12).
   - When encoding CNAME targets, append a pointer to the base-domain suffix
     inside the question name; if the offset is invalid, fail the response
     (log and raise) rather than falling back to uncompressed encoding.
   - Keep the response builder logic and error handling unchanged otherwise.
4. Update callers to use the compressed cap calculation.
   - Update `dns_server` response cap logic, `dns_client` response cap
     computation, `dns_flat_stager` caps, and the CLI helper to pass
     `base_domain` (compression required).
   - Ensure all call sites are updated in the same change to avoid stale
     calculations.
5. Manual verification.
   - Confirm CNAME responses always include compression pointers and decode correctly.
   - Confirm payload caps increase compared to uncompressed calculations.

## Testing
- Do not run tests.
