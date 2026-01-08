# CLI Complexity Reduction Phase 4B

Status: abandoned

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce cyclomatic complexity for runtime flow and logging setup in
  `sfb/cli.py` while preserving behavior, logging, and exit codes.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.

## Non-Goals
- Change module loading semantics, tunnel behavior, or shutdown handling.
- Alter log payloads or log event names.
- Run tests here.

## Decision
- Abandoned per request; defer runtime/logging complexity work until a new
  CLI complexity plan is prioritized.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Inventory runtime/logging hotspots.
   - Re-check radon findings for `run_server_command`, `run_client`,
     `_run_main`, `main`, `_configure_root_logging`, `_ensure_db_log_path`.
2. Reduce `run_server_command` complexity further.
   - Extract `_run_server_command_body(args, tunnel, logger, shutdown_requested)`
     to contain the linear happy path and return exit codes.
   - Keep wrapper `run_server_command` responsible for try/except/finally
     with the same logging and error handling.
3. Reduce `run_client` complexity.
   - Extract `_run_client_body(args, tunnel, logger, shutdown_requested)` to
     encapsulate connect, background start, and wait loop.
   - Keep the wrapper try/except/finally and logging identical.
4. Reduce `_run_main` and `main` complexity.
   - Extract helpers for log profile application and pacer summary adjustment
     to reduce branching inside `_run_main`.
   - Extract a `_start_profiler(parsed, cprofile_path)` helper for `main` to
     consolidate setup and error handling, keeping exit codes unchanged.
5. Decompose logging setup.
   - Split `_configure_root_logging` into `_configure_stdout_logging` and
     `_configure_db_logging` helpers.
   - Split `_ensure_db_log_path` into `_cleanup_db_log_file` and
     `_ensure_db_log_dir` helpers, preserving exceptions and error messages.
6. Verify by inspection.
   - Confirm log payloads, event names, and error paths are unchanged.
   - Confirm ordering: create config, apply log profile, configure logging,
     create crypto, then dispatch.

## Acceptance Criteria
- Radon no longer flags the functions above current thresholds.
- Exit codes, error handling, and shutdown behavior remain unchanged.
- Python 2.7 + 3 compatibility preserved with standard library only.

## Notes
- Keep helpers private and minimal; do not alter log event fields.
- Preserve final cleanup and `tunnel.close()` calls in all paths.

## Testing
- Do not run tests here. If needed, run `radon cc sfb/cli.py` to verify the
  complexity reductions.
