# CLI Complexity Reduction Phase 1

Status: completed

Parent Plan: CLI_COMPLEXITY_REDUCTION_PLAN.md

## Goal
- Reduce complexity in `parse_args` by extracting helper functions and
  simplifying branching while preserving CLI behavior and outputs.
- Keep two-pass parsing, argument defaults, and error conditions identical.

## Non-Goals
- Change CLI flags, defaults, or transport behaviors.
- Modify module command semantics or add new commands.
- Run tests here.

## Affected Components
- sfb/cli.py

## Detailed Steps
1. Map current parse flow.
   - Enumerate first-pass flags used (`--role`, `--transport`, `--module`).
   - Record derived values (`role_for_args`, `log_profile_explicit`,
     `generate_cert`) and how they affect parsing.
   - Note where each transport/role adds arguments and how module commands
     are registered.
2. Add small parser-building helpers.
   - `_build_base_parser(config_defaults, require_domain, require_role)` to
     build a parser with `add_common_args` and `add_module_args`.
   - `_add_transport_args(parser, config_defaults, transport, role_for_args,
     generate_cert)` to register transport-specific arguments only when
     appropriate.
   - `_add_module_commands(parser, module_cls, role_for_args, config_defaults)`
     to encapsulate subparser vs. direct registration.
3. Replace the long transport if/elif chain.
   - Implement a dispatch table keyed by transport to keep logic compact.
   - Preserve existing logic for client-only pacing args and server-only args.
   - Keep the `generate_cert` short-circuit that skips transport-specific args.
4. Keep two-pass parsing behavior identical.
   - First pass: use base parser and `parse_known_args` on the full arg list.
   - Second pass: rebuild parser with appropriate transport/role args and
     module commands, then parse the same arg list.
   - Preserve the normalization of `parsed.role` at the end.
5. Validate result parity by inspection.
   - Compare the final argument set for each transport/role combination.
   - Confirm no argument defaults or requirements changed.
   - Confirm `log_profile_explicit` and `generate_cert` handling stays the same.

## Acceptance Criteria
- `parse_args` is smaller and has fewer branches, without behavior changes.
- No new flags, no removed flags, and no altered defaults.
- Two-pass parsing and normalization remain intact.

## Notes
- Keep helper names and signatures private and minimal.
- Avoid introducing new module-level state.

## Testing
- Do not run tests here. If needed, use `radon cc sfb/cli.py` to verify the
  complexity drop after the refactor.

## Execution Notes
- 20260108: Added base-parser and module/transport helpers, replaced the
  transport chain with a dispatch table, and verified two-pass parsing and
  generate-cert gating by inspection; no tests run.
