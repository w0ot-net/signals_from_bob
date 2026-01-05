# Port Forward Module Plan

## Summary
Add a `port_fwd` module that listens on a Bob-side TCP address and forwards
each inbound connection to a fixed Alice-side target address using a dedicated
tunnel channel per connection.

## Goals
- Provide a simple CLI: `--module port_fwd --local HOST:PORT --remote HOST:PORT`.
- Relay bytes bidirectionally with backpressure and clean half-close behavior.
- Keep Python 2.7/3 compatibility and Windows + Linux support.
- Document the protocol and module usage in the module docs.

## Affected Components
- `sfb/modules/port_fwd/` (new module implementation and helpers)
- `sfb/modules/__init__.py` (register module)
- `sfb/config.py` (module defaults + validation + logging toggle)
- `sfb/logging_util.py` (component filter for port_fwd logs)
- `sfb/log_profiles.py` (module log toggle defaults per profile)
- `sfb/cli.py` (log snapshot includes new module toggle)
- `doc/CONTROL_MESSAGES.md` (document `fwd` control messages)
- `doc/MODULES.md` (module overview + flow)
- `doc/ARCHITECTURE.md` (module directory layout)
- `doc/PORT_FWD.md` (new module spec and examples)

## Plan
1. Define the port forward control messages in a new helper module:
   - Message type: `fwd`.
   - Commands: `connect`, `connect_ok`, `err`.
   - Fields: `rid`, `ch`, `host`, `port`, plus error `code` + `reason`.
2. Implement the `PortForwardModule` (name `port_fwd`):
   - Register CLI args on Bob only: `--local`, `--remote`, optional `--backlog`
     and `--timeout` settings if needed.
   - Parse host:port for IPv4, IPv6 (`[::1]:port`), and DNS names.
   - Start a TCP listener on Bob, accept connections, open a new tunnel channel
     per connection, and send `fwd/connect`.
3. Add Alice-side connect handling:
   - On `fwd/connect`, open the target TCP socket using `getaddrinfo`.
   - Send `fwd/connect_ok` on success or `fwd/err` on failure.
   - On success, start a relay between the socket and the channel.
4. Build the relay/pump helpers:
   - Reuse the socket/channel pump pattern from SOCKS or extract a shared
     helper so port_fwd can log with `fwd.*` event names.
   - Ensure EOF propagation uses `channel.close_write()` and socket shutdown.
   - Track per-connection state and clean up on errors or close.
5. Wire in logging and config:
   - Add `log_component_module_port_fwd` toggle and include it in profiles.
   - Add port_fwd timeouts/buffer defaults as needed in `Config` and validate.
6. Update docs:
   - Add `doc/PORT_FWD.md` with protocol flow, CLI usage, and limitations.
   - Update `doc/MODULES.md`, `doc/CONTROL_MESSAGES.md`, and
     `doc/ARCHITECTURE.md` to include the new module.

## Success Criteria
- `port_fwd` starts on Bob and forwards traffic to Alice's target reliably.
- Failures send `fwd/err` and close channels without leaking threads.
- Logs are filterable via the new module log toggle.
- Documentation fully describes the module and its control messages.
