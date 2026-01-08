# CLI Complexity Reduction Phase 4A

Status: abandoned

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce cyclomatic complexity for CLI parsing and config plumbing in
  `sfb/cli.py` while preserving behavior, defaults, and log payloads.
- Keep Python 2.7 + 3 compatibility and standard-library-only constraints.

## Non-Goals
- Change CLI flags, defaults, or transport behaviors.
- Modify logging fields or crypto behavior.
- Run tests here.

## Decision
- Abandoned per request; defer further parsing/config complexity work until
  a new CLI complexity plan is prioritized.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Inventory parsing/config hotspots.
   - Re-check radon findings for `parse_args`, `create_config`,
     and `_wrap_lossy_transport`.
2. Further reduce `parse_args` complexity.
   - Extract `_parse_first_pass(arg_list)` to return partial args and
     derived values (role, transport, role_for_args).
   - Extract `_parse_second_pass(arg_list, config_defaults, role_for_args,
     transport, generate_cert, partial_args)` to build the second parser and
     return parsed args.
   - Keep log profile and generate-cert detection as-is.
3. Simplify `create_config` orchestration.
   - Add a small `_get_transport_builder(transport)` helper and move the
     transport mapping into it.
   - Add a small `_merge_role_config(config_kwargs, args)` helper that applies
     client/server config in place to reduce branching.
4. Reduce `_wrap_lossy_transport` branching.
   - Add a `_lossy_enabled(args)` helper that returns the early-exit boolean.
   - Ensure `stats_enabled` and log payloads remain identical.
5. Verify by inspection.
   - Confirm parsing defaults, required flags, and `None` filtering remain
     identical.
   - Confirm lossy transport logging payload fields and values are unchanged.

## Acceptance Criteria
- Radon no longer flags `parse_args`, `create_config`, or `_wrap_lossy_transport`
  at the current thresholds.
- CLI behavior, defaults, and logging payloads remain identical.
- Python 2.7 + 3 compatibility preserved with standard library only.

## Notes
- Keep helpers private and small; avoid reordering logic unless neutral.
- Preserve two-pass parsing and role normalization semantics.

## Testing
- Do not run tests here. If needed, run `radon cc sfb/cli.py` to verify the
  complexity reductions.
