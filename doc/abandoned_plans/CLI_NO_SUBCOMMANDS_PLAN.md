# CLI No-Subcommands Plan

Status: abandoned

## Goal
- Remove argparse subcommand support from sfb/cli.py so module options are flat
  flags.
- Keep module runtime behavior and defaults unchanged; only CLI syntax changes.

## Non-Goals
- Changing transport behavior or module protocol behavior.
- Updating or running tests.

## Decision
- Abandoned per request; no implementation work executed.

## Current Subcommands (to remove)
- file_transfer: list, hash, get, put (per-command positional args plus
  optional --timeout).
- port_fwd_server: start (requires --local and --remote).
- Note: socks and nc_linux already run without subcommands.

## Affected Components
- sfb/cli.py
- sfb/modules/base_module.py
- sfb/modules/file_transfer/file_transfer.py
- sfb/modules/port_fwd/port_fwd_server.py
- sfb.py
- doc/architecture/PORT_FWD.md
- doc/fixed_bugs/file_transfer_put_timeout_fast_poll.md
- Any other call sites found via repo sweep for --module file_transfer and
  --module port_fwd_server.

## Design Proposal
- Remove parser.add_subparsers usage in sfb/cli.py; always pass the module
  parser directly to register_commands.
- Drop DEFAULT_COMMAND, REQUIRES_COMMAND, and USES_SUBCOMMANDS handling in
  sfb/cli.py and sfb/modules/base_module.py.
- File transfer CLI becomes flag-based with a required action selector, e.g.
  --file-op {list,hash,get,put} (or a mutually exclusive set of
  --file-list/--file-hash/--file-get/--file-put), and explicit args:
  - list/hash: --path required.
  - get: --remote required, --local optional.
  - put: --local and --remote required.
  - --timeout remains optional and shared.
- Port forward server CLI becomes flag-based with required --local and --remote.
- Update module run_command implementations to use the new args fields and
  error messages when required options are missing.
- Update all doc/examples and scripts to use the new flag-style invocations.

## Plan
1. Refactor sfb/cli.py to remove subparser creation and command resolution;
   keep module loading intact while parsing module args directly.
2. Update sfb/modules/file_transfer/file_transfer.py and
   sfb/modules/port_fwd/port_fwd_server.py to register flat CLI args and switch
   run_command logic to the new fields.
3. Remove unused subcommand-related class attributes from
   sfb/modules/base_module.py and module classes.
4. Sweep for --module file_transfer and --module port_fwd_server call sites and
   convert them to the new flag-style syntax in sfb.py and docs.

## Testing
- Do not run tests here. The user will run tests as needed with python3.
