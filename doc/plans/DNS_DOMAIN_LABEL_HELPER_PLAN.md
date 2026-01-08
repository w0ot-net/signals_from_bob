# DNS Domain Label Helper Plan

## Goal
- Remove duplicated domain normalization/splitting/label validation in DNS
  query/CNAME encode/decode paths by introducing a shared helper.
- Keep behavior and errors consistent across query and CNAME handling.

## Non-Goals
- Change DNS encoding rules, label limits, or error messages beyond the
  refactor.
- Modify TXT/NULL/other record handling or MTU calculations beyond the helper
  refactor.
- Run tests here.

## Affected Components
- sfb/transport/dns/dns_codec.py

## Plan
1. Add helper `_split_domain_labels(name, lower=False, require_non_empty=False,
   empty_error=None)` in `sfb/transport/dns/dns_codec.py`.
   - Normalize with `_normalize_domain`, optionally lowercase, split on `.`,
     drop empty labels, and call `_validate_labels`.
   - If `require_non_empty`, raise `ValueError(empty_error)` when no labels are
     present.
2. Replace duplicated logic in encode/decode paths:
   - `encode_query_name()`: use the helper for `base_labels` with
     `require_non_empty=True` and `empty_error='base_domain required'`.
   - `decode_query_name()`: use the helper for `base_parts` and `name_parts`
     with lowercase enabled.
   - `encode_cname_target()`: use the helper for `suffix_labels` (no required
     check).
   - `decode_cname_target()`: use the helper for `suffix_parts` and
     `name_parts` with lowercase enabled.
3. Confirm behavior parity:
   - Trailing dot stripping and empty-label collapsing remain unchanged.
   - Error messages for missing base domain, suffix mismatch, and label length
     checks stay the same.
   - Data-label `label_max_len` checks remain in the callers.

## Testing
- Do not run tests here. The user will run tests with python3 if needed.

## Notes
- Keep `_normalize_domain` for other callers; the helper stays a thin wrapper
  to reduce duplication.
