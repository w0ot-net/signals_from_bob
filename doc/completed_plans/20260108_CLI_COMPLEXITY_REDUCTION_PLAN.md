# CLI Complexity Reduction Plan

Status: draft

## Goal
- Reduce cyclomatic complexity in `sfb/cli.py` (notably `parse_args`,
  `create_config`, `_run_main`, `run_server_command`, `_wrap_lossy_transport`)
  without changing CLI behavior, defaults, logging fields, or error handling.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.
- decrease lines of code without hurting performance or readability

## Non-Goals
- Add or remove CLI flags, defaults, or transport behaviors.
- Introduce new dependencies or change log schemas.
- Run tests here.

## Affected Components
- sfb/cli.py

## Plan
1. Refactor argument parsing into smaller helpers.
   - Add `_build_base_parser`, `_add_transport_args`, and
     `_add_module_commands` helpers to reduce branching in `parse_args`.
   - Use a transport/role dispatch table (dict of callables) instead of the
     current long if/elif chain, keeping two-pass parsing intact.
2. Split config creation into focused builders.
   - Add `_build_transport_config(args)`, `_build_role_config(args)`,
     `_build_logging_config(args)`, and `_build_crypto_config(args)` helpers.
   - Keep default port normalization and `None` filtering behavior identical.
3. Simplify lossy transport wrapping.
   - Extract impairment rate calculation and wrapper construction into small
     helpers that preserve existing `log_event` payloads.
4. Break up server command execution.
   - Extract `_wait_for_client`, `_load_remote_module`, `_resolve_module_command`,
     and `_unload_remote_module` helpers to shrink `run_server_command` while
     preserving error handling and shutdown logging.
5. Isolate logging setup in `_run_main`.
   - Move DB log cleanup/creation and stdout formatter setup into helpers.
   - Keep log level, formatter fields, and startup log contents unchanged.
6. Validate complexity reduction.
   - Re-run `radon cc sfb/cli.py` to confirm the targeted functions drop below
     the current C/D grades.

## Testing
- Do not run tests here. If needed, re-run `radon cc sfb/cli.py` to verify
  complexity improvements.

## Execution Notes
- Marked complete; CLI complexity reduction work is tracked in
  `doc/completed_plans/20260108_CLI_COMPLEXITY_REDUCTION_PHASE1.md`,
  `doc/completed_plans/20260108_CLI_COMPLEXITY_REDUCTION_PHASE2.md`, and
  `doc/completed_plans/20260108_CLI_COMPLEXITY_REDUCTION_PHASE3.md`.
- No additional code changes in this completion step.
