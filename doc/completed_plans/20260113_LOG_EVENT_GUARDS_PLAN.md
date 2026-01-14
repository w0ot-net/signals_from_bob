# Log Event Guard Plan

## Goal
- Add logger level guards around all `log_event(...)` call sites so field lambdas
  are only created when the log level is enabled.
- Preserve existing log semantics (levels, events, messages, fields) and runtime
  behavior.

## Non-Goals
- Changing event names, log levels, formats, or log filtering behavior.
- Refactoring unrelated code paths.
- Running tests (user will run with python3 as needed).

## Affected Components
- sfb/channel/channel_manager.py
- sfb/channel/channel.py
- sfb/cli.py
- sfb/modules/base_module.py
- sfb/modules/file_transfer/file_transfer.py
- sfb/modules/nc_linux/nc_linux_pump.py
- sfb/modules/nc_linux/nc_linux.py
- sfb/modules/port_fwd/port_fwd_relay.py
- sfb/modules/port_fwd/port_fwd_server.py
- sfb/modules/relay_connection.py
- sfb/modules/relay_pump.py
- sfb/modules/socks/socks_relay.py
- sfb/modules/socks/socks_server.py
- sfb/protocol/__init__.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_flat_stager.py
- sfb/transport/dns/dns_server.py
- sfb/transport/dns/dns_utils.py
- sfb/transport/icmp/icmp_client.py
- sfb/transport/icmp/icmp_server.py
- sfb/transport/memory/memory_client.py
- sfb/transport/memory/memory_server.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_server.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake/tls_handshake_server.py
- sfb/transport/udp_ephemeral/udp_ephemeral_client.py
- sfb/transport/udp_ephemeral/udp_ephemeral_server.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/module_loader.py

## Design Proposal
- Wrap each `log_event(...)` invocation with
  `if <logger>.isEnabledFor(<level>):` using the same logger instance and level
  already passed to `log_event`.
- Keep guard scopes minimal and readable; group adjacent log events under a
  single guard only when they share the same logger and level.
- Avoid any new helper wrappers that would still allocate field lambdas when
  logging is disabled.

## Plan
1. Inventory every `log_event(...)` call site in the affected components and
   confirm the logger instance and level used at each site.
2. Add `isEnabledFor` guards and move field lambdas inside the guarded block so
   lambda creation is skipped when logging is disabled.
3. Consolidate guards for adjacent same-level log events where it improves
   readability without changing control flow.
4. Sanity check for any missing `logging` imports needed by the new guards.

## Testing
- Do not run tests here. The user will run tests as needed with python3.

## Execution Notes
- 20260113: Wrapped every `log_event(...)` call with a per-level
  `logger.isEnabledFor(level)` guard; kept per-call guards rather than grouping
  adjacent calls to keep the change mechanical and predictable.
