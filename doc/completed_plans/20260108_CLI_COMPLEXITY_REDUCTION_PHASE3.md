# CLI Complexity Reduction Phase 3

Status: completed

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce complexity in `run_server_command` and `_run_main` by extracting
  helpers while preserving behavior, logging, and shutdown semantics.
- Keep error handling and exit codes unchanged.

## Non-Goals
- Change module loading semantics, tunnel behavior, or logging output.
- Alter signal handling or background thread behavior.
- Run tests here.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Factor module lifecycle helpers.
   - `_wait_for_client(tunnel, args, logger, shutdown_requested)` to encapsulate
     the connect-wait loop and related logging.
   - `_load_remote_module(tunnel, args, logger, module_loader)` to load the
     remote module and emit the same log events.
   - `_resolve_module_command(args, module_cls, logger)` to apply default command
     selection and command-required checks without changing error paths.
   - `_unload_remote_module(tunnel, args, module_loader)` to best-effort unload.
2. Reduce `run_server_command` branching.
   - Use the helpers above to keep the main flow linear:
     start background, wait for client, load module, allow message type,
     resolve command, run command, unload module.
   - Preserve all log events, event names, and fields.
   - Keep ModuleError handling and logging as-is.
3. Extract logging setup from `_run_main`.
   - `_configure_root_logging(parsed, cprofile_path)` to set log level,
     stdout handler formatting, and optional DB logging.
   - `_ensure_db_log_path(parsed)` to handle deletion/creation logic and
     directory preparation while keeping errors identical.
   - `_log_startup(logger, parsed, cprofile_path, config)` to emit the startup
     snapshot with the same payload structure.
4. Keep control flow in `_run_main` intact.
   - Preserve `--db-log` default behavior, log profile application, and
     `tunnel_pacer_summary_interval` adjustments.
   - Keep the order: parse config, apply log profile, setup logging, create
     crypto, then dispatch to `run_server`/`run_client`.
5. Review for unintended behavioral changes.
   - Verify that logging is configured before `create_crypto` as before.
   - Confirm `tunnel.close()` is still called in all finally blocks.

## Acceptance Criteria
- `run_server_command` and `_run_main` are shorter and easier to follow.
- All existing log events and error handling remain unchanged.
- Exit codes and shutdown behavior match current behavior.

## Notes
- Keep helpers private and reuse existing log_event calls.
- Avoid reordering operations unless the order is proven to be behavior-neutral.

## Testing
- Do not run tests here. If needed, use `radon cc sfb/cli.py` to verify the
  complexity drop after the refactor.

## Execution Notes
- 20260108: Added module lifecycle and logging helpers, refactored
  `run_server_command` and `_run_main` to use them, and preserved logging/event
  ordering by inspection; no tests run.
