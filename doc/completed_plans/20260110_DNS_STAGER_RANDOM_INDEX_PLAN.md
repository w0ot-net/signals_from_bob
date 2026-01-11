# DNS Stager Random Index Mapping Plan

Status: completed

## Summary
Replace sequential numeric piece labels with a per-run deterministic
mapping so index labels look random but remain reversible for Bob. The
stager and server will share a seed embedded in the generated stager
blob; Bob will not be queried for any hash or seed.

## Goals
- Make per-piece labels non-numeric and randomized per run, while keeping
  deterministic decoding on Bob.
- Keep the cache-buster in the first label and maintain stable label
  lengths for payload sizing.
- Preserve Python 2/3 compatibility and standard library only in stagers.
- Minimize added complexity and logging overhead.

## Non-Goals
- Cryptographic secrecy or anti-forensics.
- Per-request randomized mappings (the cache-buster already changes per
  request).
- Transport changes outside the DNS stager path.
- Automated tests.

## Affected Components
- `sfb/cli.py`
- `sfb/config.py`
- `sfb/stagers/dns_stager.py`
- `sfb/stagers/dns_stager_template.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/transport/dns/dns_server.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
### Phase 1: Seed generation and payload sizing
- Add a per-run seed (e.g., `dns_flat_index_seed`) to config; generate it
  during `--stager` preparation and store it alongside `dns_flat_*`.
- Define a fixed-length index token (e.g., 8 hex chars) to keep qname
  lengths stable.
- Update `_calc_flat_payload_cap` to size against the longest stager
  qname (`<cache>.<index_token>.<base_domain>`).

### Phase 2: Stager encoding
- Add a template placeholder for the seed (e.g., `{{INDEX_SEED}}`) and
  embed it into the stager one-liner.
- Implement a reversible mapping such as:
  - `token = (index ^ seed) & 0xFFFFFFFF`
  - `label = "%08x" % token`
- Keep the per-request cache-buster label first, and continue to use
  `count` as the reserved selector label.
- Ensure retries reuse the same cache-buster/label for the request that
  is being retried.

### Phase 3: Server decoding and validation
- Update `DnsFlatStager` to parse `<cache>.<token>.<base>` and decode the
  index using the same seed.
- Validate decoded index is within `[1, flat_count]`, else respond with
  `flat_invalid`.
- Keep cache-buster validation (non-base32 label, `r-` prefix) to avoid
  collisions with tunnel queries.
- Extend `dns.stager_config` logging to include the seed (or a truncated
  form) for debugging.

### Phase 4: Regeneration and docs
- Regenerate `linux_dns_stager.txt` and `windows_dns_stager.txt` on
  every `--stager` run using the new mapping.
- Note in docs that old stager blobs are incompatible and must be
  regenerated.

## Testing
- Do not run tests.

## Execution Notes
- Added a per-run index seed to stager generation with fixed-length hex tokens.
- Updated Bob's stager decoder to validate and reverse the seeded mapping.
- Regenerated DNS stager one-liners and documented the incompatibility with older blobs.
