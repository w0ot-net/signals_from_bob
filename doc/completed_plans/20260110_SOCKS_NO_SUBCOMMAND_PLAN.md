# SOCKS No-Subcommand Plan

## Goal
- Remove the SOCKS module subcommand requirement so `--module socks` works
  without `start` in both Python 2 and Python 3.
- Preserve SOCKS behavior, defaults, and runtime flow; only CLI invocation
  changes.

## Non-Goals
- Changing SOCKS protocol behavior, relay behavior, or defaults.
- Adding new CLI flags or changing existing flag names.
- Running tests (user will run as needed).

## Affected Components
- sfb/modules/socks/socks_server.py
- scripts/icmp_socks_diag.py
- scripts/icmp_socks_test.py
- scripts/icmp_socks_scp_test.py

## Design Proposal
- Mark `SocksServerModule.USES_SUBCOMMANDS = False` and remove
  `DEFAULT_COMMAND`/`REQUIRES_COMMAND` to make the module non-subcommand.
- Update `register_commands` to add `--socks-host`/`--socks-port` directly to
  the module parser instead of a `start` subparser.
- Keep `run_command` unchanged aside from relying on the new top-level args.
- Remove the literal `start` token from scripts that spawn the SOCKS module.

## Plan
1. Update `sfb/modules/socks/socks_server.py` to disable subcommands and attach
   SOCKS args directly to the module parser.
2. Remove `start` from SOCKS module invocations in:
   - `scripts/icmp_socks_diag.py`
   - `scripts/icmp_socks_test.py`
   - `scripts/icmp_socks_scp_test.py`
3. Sweep for any remaining `--module socks start` usage and update those call
   sites to match the new no-subcommand invocation.

## Testing
- Do not run tests here. The user will run tests as needed with python3.

## Execution Notes
- Set the SOCKS server module to non-subcommand mode and moved SOCKS CLI args to
  the module parser.
- Removed the `start` token from SOCKS launch scripts and the sfb.py usage
  example.
