# Port Forward Module Plan

## Summary
Refactor the SOCKS relay/pump helpers into shared modules under `sfb/modules`,
rename SOCKS-specific relay config/log toggles to generic names, and implement
a `port_fwd` module that reuses the shared relay/pump stack with minimal new
knobs.

## Goals
- Keep port_fwd behavior aligned with SOCKS relay patterns: channel per
  connection, bidirectional relay, clean half-close.
- Reuse the existing SOCKS relay/pump config knobs (renamed to generic names)
  and avoid new tuning flags for port_fwd.
- Maintain Python 2.7/3 compatibility and Windows + Linux support.
- Document control messages and module usage.

## Affected Components
- `sfb/modules/relay_logging.py` (new shared logging helpers)
- `sfb/modules/relay_pump.py` (moved + generalized data pump)
- `sfb/modules/relay_connection.py` (moved + generalized relay manager)
- `sfb/modules/relay_control_messages.py` (new shared connect/err helpers)
- `sfb/modules/socks/` (update imports, config names, log/event prefixes)
- `sfb/modules/port_fwd/` (new server/relay modules + logging glue)
- `sfb/modules/__init__.py` (register port_fwd modules)
- `sfb/config.py` (rename SOCKS relay config knobs to generic names)
- `sfb/cli.py` (rename hidden relay tuning flags, log snapshot keys)
- `sfb/logging_util.py` (rename component toggle and cover socks + port_fwd)
- `sfb/log_profiles.py` (update profile keys/patterns)
- `scripts/icmp_socks_diag.py` and related scripts (update tuning flag names)
- `doc/CONTROL_MESSAGES.md`, `doc/MODULES.md`, `doc/ARCHITECTURE.md`
- `doc/SOCKS.md`, `doc/LOGGING.md`, `doc/PORT_FWD.md` (new)
- `doc/bugs/*` entries that mention renamed config knobs (update as needed)

## Plan
1. Extract SOCKS logging helpers into a generic relay logging module:
   - Move `sfb/modules/socks/socks_logging.py` to `sfb/modules/relay_logging.py`.
   - Rename `sock_fields` to `relay_fields`; keep `add_field`, `add_fields`,
     `normalize_peer`, and `duration_secs` unchanged.
   - Update all SOCKS imports/call sites to use `relay_logging` and
     `relay_fields` (no compatibility shims).

2. Move and generalize the data pump:
   - Move `sfb/modules/socks/data_pump.py` to `sfb/modules/relay_pump.py`.
   - Replace hard-coded `sock.*` event names with a `event_prefix` parameter
     (default `'sock'` for SOCKS, `'fwd'` for port_fwd).
   - Swap config access from `socks_*` to renamed generic relay settings
     (see step 5).
   - Keep existing semantics for backoff, EOF handling, half-close, and
     socket/channel shutdown.

3. Move and generalize the relay connection manager:
   - Move `sfb/modules/socks/relay_connection.py` to
     `sfb/modules/relay_connection.py`.
   - Parameterize log event prefix and label strings so SOCKS can keep its
     current log names while port_fwd uses `fwd.*`.
   - Update SOCKS modules to import the new `RelayConnection`.

4. Unify connect control messages:
   - Add `sfb/modules/relay_control_messages.py` with helpers:
     `relay_connect(msg_type, rid, ch, host, port)`,
     `relay_connect_ok(msg_type, rid, ch, extra=None)`,
     `relay_err(msg_type, rid, ch, code, reason)`.
   - Update SOCKS to use `msg_type='sock'` and port_fwd to use
     `msg_type='fwd'`, keeping per-module event prefixes/logging intact.

5. Rename SOCKS relay config knobs to generic relay knobs:
   - Mapping (examples, adjust consistently across code/docs):
     - `socks_listen_host` -> `relay_listen_host`
     - `socks_listen_port` -> `relay_listen_port`
     - `socks_listen_backlog` -> `relay_listen_backlog`
     - `socks_accept_timeout` -> `relay_accept_timeout`
     - `socks_channel_open_timeout` -> `relay_channel_open_timeout`
     - `socks_connect_timeout` -> `relay_connect_timeout`
     - `socks_connect_target_timeout` -> `relay_target_connect_timeout`
     - `socks_relay_socket_timeout` -> `relay_socket_timeout`
     - `socks_relay_channel_timeout` -> `relay_channel_timeout`
     - `socks_relay_write_timeout` -> `relay_write_timeout`
     - `socks_relay_buffer_size` -> `relay_buffer_size`
     - `socks_pump_poll_timeout` -> `relay_pump_poll_timeout`
     - `socks_pump_backoff_max` -> `relay_pump_backoff_max`
     - `socks_thread_join_timeout` -> `relay_thread_join_timeout`
   - Update validation in `sfb/config.py` and all call sites in SOCKS and the
     new port_fwd module.
   - Rename hidden CLI tuning flags to match (e.g.,
     `--relay-buffer-size`, `--relay-pump-backoff-max`) and update scripts
     that pass the old flags.

6. Update logging component toggles to cover both modules:
   - Rename `log_component_module_socks` to a generic name
     (e.g., `log_component_module_relay`) and update `sfb/logging_util.py`,
     `sfb/log_profiles.py`, and the CLI log snapshot.
   - Extend the component filter so one toggle gates both `sock.*` and
     `fwd.*` event prefixes and logger names.
   - Add `fwd.*` event patterns to relevant log profiles when port_fwd
     troubleshooting is desired.

7. Implement the port forward modules using shared relay code:
   - `PortForwardServerModule` (Bob):
     - CLI: `--local HOST:PORT` and `--remote HOST:PORT` required; do not
       add per-module backlog/timeouts (use shared relay config).
     - Accept connections on `--local`, allocate `rid`, open a channel,
       send `fwd/connect` with target `host`/`port`.
     - Wait for `connect_ok`/`err` using the same pending-request pattern
       as SOCKS (extract helper if needed to avoid duplication).
     - On success, start a `RelayConnection` with `event_prefix='fwd'`;
       on failure, close socket/channel and log the error.
   - `PortForwardRelayModule` (Alice):
     - Handle `fwd/connect`, resolve target with `getaddrinfo`, connect
       with `relay_target_connect_timeout`.
     - Send `connect_ok` or `err`, then start a `RelayConnection` on success.
   - Use shared pump behavior for half-close/EOF, and keep thread naming
     consistent (`fwd-ridX-*`).

8. Documentation updates:
   - `doc/CONTROL_MESSAGES.md`: document `fwd/connect`, `fwd/connect_ok`,
     and `fwd/err` fields and flow.
   - `doc/MODULES.md` and `doc/ARCHITECTURE.md`: note shared relay helpers
     and the new port_fwd modules.
   - `doc/SOCKS.md` and `doc/LOGGING.md`: update renamed config keys and
     component toggle names.
   - Add `doc/PORT_FWD.md`: usage, flow diagram, limitations, and examples.
   - Sweep `doc/bugs/*` and scripts for renamed config keys and update as
     needed to keep troubleshooting docs accurate.

## Success Criteria
- Both SOCKS and port_fwd use the shared relay/pump helpers in `sfb/modules/`.
- Port_fwd works without introducing new tuning knobs beyond the shared relay
  settings.
- Log filtering and profiles can include or exclude both SOCKS and port_fwd
  using a single generic module toggle.
- Documentation reflects renamed config keys and the new port_fwd module.
