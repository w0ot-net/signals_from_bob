# CLI Complexity Reduction Phase 4C

Status: abandoned

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce cyclomatic complexity for TLS bump and ASN.1 helper functions in
  `sfb/cli.py` while preserving binary output and error handling.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.

## Non-Goals
- Change TLS bump certificate semantics or output template format.
- Modify error messages, exit codes, or logging fields.
- Run tests here.

## Decision
- Abandoned per request; defer TLS bump/ASN.1 complexity work until a new
  CLI complexity plan is prioritized.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Inventory TLS/ASN.1 hotspots.
   - Re-check radon findings for `_handle_tls_bump_generate_cert`,
     `_read_der_length`, `_mark_cn_nodes`, and `_encode_node`.
2. Decompose `_handle_tls_bump_generate_cert`.
   - Extract `_parse_tls_bump_cn_len(value)` to validate and return integer
     values with the same error messages and exit codes.
   - Extract `_generate_tls_bump_template(cn_len)` to perform generation and
     return the output tuple for writing.
3. Decompose DER helpers.
   - Split `_read_der_length` into `_read_der_length_short` and
     `_read_der_length_long` helpers that share error paths.
   - Split `_mark_cn_nodes` into a predicate helper to detect the CN OID pair
     while keeping recursion behavior unchanged.
   - Split `_encode_node` into helpers for encoding children and offset
     normalization to reduce branching.
4. Verify by inspection.
   - Confirm offsets, byte lengths, and output bytes remain identical.
   - Confirm error messages and raised exceptions are unchanged.

## Acceptance Criteria
- Radon no longer flags the functions above current thresholds.
- TLS bump template output and DER encoding behavior remain identical.
- Python 2.7 + 3 compatibility preserved with standard library only.

## Notes
- Keep helpers private and small; avoid reordering operations unless neutral.
- Preserve existing exception types and messages exactly.

## Testing
- Do not run tests here. If needed, run `radon cc sfb/cli.py` to verify the
  complexity reductions.
