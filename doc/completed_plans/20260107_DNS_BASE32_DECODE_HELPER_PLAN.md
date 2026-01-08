# DNS Base32 Decode Helper Plan

## Goal
- Remove duplicated suffix stripping, label-length validation, and base32 decode
  logic in DNS query name vs CNAME target decoding by introducing a shared
  helper.
- Keep behavior and error messages identical to the current decode paths.

## Non-Goals
- Change base32 encoding/decoding rules, label length constraints, or error
  message text beyond the refactor.
- Modify other record encoders/decoders or MTU calculations.
- Run tests here.

## Affected Components
- sfb/transport/dns/dns_codec.py

## Plan
1. Add helper `_decode_b32_labels(name, suffix, label_max_len, skip_first=False,
   err_suffix=None, err_no_data=None)` in `sfb/transport/dns/dns_codec.py`.
   - Normalize `label_max_len` with `_normalize_label_max_len`.
   - Use `_split_domain_labels(..., lower=True)` for both `name` and `suffix`.
   - If `suffix` is non-empty, verify the suffix matches and raise
     `ValueError(err_suffix)` on mismatch.
   - Select `data_parts` by removing the suffix parts, and optionally
     `skip_first` (nonce label) for query names.
   - Validate each label length against `label_max_len` and raise the existing
     `ValueError('Label exceeds max length')` on violations.
   - Concatenate labels; raise `ValueError(err_no_data)` when empty; return
     `base32_decode` of the concatenated string.
2. Replace `decode_query_name()` logic with a call to the helper.
   - Use `skip_first=True`,
     `err_suffix='Query name does not match base domain'`,
     `err_no_data='No data labels in query name'`.
3. Replace `decode_cname_target()` logic with a call to the helper.
   - Use `skip_first=False`,
     `err_suffix='CNAME target does not match suffix'`,
     `err_no_data='No data labels in CNAME target'`.
4. Confirm behavior parity:
   - Suffix matching and error strings are unchanged.
   - Empty data detection still yields the same errors.
   - Label-length checks still use the normalized `label_max_len`.

## Testing
- Do not run tests here. The user can run python3 tests if needed.

## Execution Notes
- Added `_decode_b32_labels` helper and routed query/CNAME decode paths through
  it while preserving error ordering for the query-name base-domain checks.
- Tests not run (not requested).
