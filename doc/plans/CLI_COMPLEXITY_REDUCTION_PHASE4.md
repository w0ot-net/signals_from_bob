# CLI Complexity Reduction Phase 4

Status: draft

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce cyclomatic complexity for the remaining radon hotspots in
  `sfb/cli.py` while preserving behavior, logging, and error handling.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.

## Non-Goals
- Change CLI flags, defaults, or transport behaviors.
- Modify TLS bump certificate semantics or log payloads.
- Run tests here.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Inventory the remaining complexity hotspots.
   - Track the radon-reported functions: `parse_args`, `create_config`,
     `_wrap_lossy_transport`, `run_server_command`, `run_client`, `_run_main`,
     `_configure_root_logging`, `_ensure_db_log_path`, `main`,
     `_handle_tls_bump_generate_cert`, `_read_der_length`, `_mark_cn_nodes`,
     `_encode_node`.
2. Split CLI parsing/config paths further.
   - Factor `parse_args` into first-pass/second-pass helpers and a small
     role/transport resolver to drop branching in the top-level function.
   - Use dispatch tables or small helpers in `create_config` and
     `_wrap_lossy_transport` so each function is mostly orchestration.
3. Reduce runtime flow complexity.
   - Extract `run_client` and `run_server_command` bodies into helpers that
     return exit codes; keep try/except handling and log events identical in
     the wrapper functions.
   - Break `_run_main` into small preflight helpers to keep the order:
     config creation, log profile application, logging setup, crypto creation,
     then dispatch.
4. Decompose logging setup.
   - Split `_configure_root_logging` into stdout handler setup and DB handler
     setup helpers, with `_ensure_db_log_path` delegating file and directory
     cleanup to smaller helpers.
5. Decompose TLS bump certificate helpers.
   - Split `_handle_tls_bump_generate_cert` into validation, generation, and
     write helpers with the same error messages and exit codes.
   - Refactor `_read_der_length`, `_mark_cn_nodes`, and `_encode_node` into
     smaller pure helpers while preserving binary output and offsets.
6. Verify behavior and complexity by inspection.
   - Confirm log payloads, defaults, and error paths are unchanged.
   - Ensure helpers remain private and ASCII-only in code.

## Acceptance Criteria
- Radon no longer flags the functions above current thresholds.
- No changes to CLI behavior, logging fields, or TLS bump output semantics.
- Python 2.7 + 3 compatibility preserved with standard library only.

## Notes
- Avoid reordering operations unless behavior-neutral.
- Keep helpers private and small; reuse existing log_event messages.

## Testing
- Do not run tests here. If needed, run `radon cc sfb/cli.py` to verify the
  complexity reductions.
