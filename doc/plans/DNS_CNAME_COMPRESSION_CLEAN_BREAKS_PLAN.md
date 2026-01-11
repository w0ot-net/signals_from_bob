# DNS CNAME Compression Clean Breaks Plan

Status: draft

## Summary
Make CNAME compression mandatory and remove uncompressed fallbacks when
executing `doc/plans/DNS_CNAME_COMPRESSION_PLAN.md`.

## Related Plans
- `doc/plans/DNS_CNAME_COMPRESSION_PLAN.md` (execute together)

## Goals
- Always use compressed CNAME responses (no uncompressed fallback).
- Use one compressed RDATA helper for response encoding and payload-cap lookup.
- Keep response payload caps aligned with compressed encoding.

## Non-Goals
- Change query encoding or record types.
- Add feature flags or toggles.
- Modify tests.

## Affected Components
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/cli.py`

## Plan
1. Make compression mandatory in cap calculations.
   - Change `calc_cname_response_payload_cap` to require the inputs needed to
     compute compression offsets (no `use_compression` flag).
   - Update call sites in the server, client, flat stager, and CLI to pass the
     new arguments so caps match the new encoding.
2. Centralize compressed CNAME RDATA encoding.
   - Add a helper in `dns_codec` that builds compressed CNAME RDATA bytes and
     validates pointer offsets.
   - Update response building and payload-cap lookup to use this helper instead
     of `encode_cname_target` + `encode_name`.
3. Treat pointer offset failures as hard errors.
   - Remove any uncompressed fallback path in the server response builder.
   - Keep logging consistent when rejecting responses due to offset errors.
4. Keep plan cross-references aligned.
   - Maintain cross-links between this plan and
     `doc/plans/DNS_CNAME_COMPRESSION_PLAN.md`.

## Testing
- Do not run tests.
