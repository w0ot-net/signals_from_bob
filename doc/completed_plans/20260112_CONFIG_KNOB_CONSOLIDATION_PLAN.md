# Channel + Relay Config Consolidation Plan

Status: completed

## Summary
Reduce channel/relay config knobs and align ownership by consolidating duplicate
fields, moving SOCKS-specific listen settings under a SOCKS name, and deriving
pump timeouts from existing backoff settings. Preserve current behavior where
possible.

## Goals
- Reduce configuration knobs without losing meaningful tuning.
- Align ownership: channel_* for channel behavior, relay_* for shared relay,
  socks_* for SOCKS-only settings.
- Keep runtime behavior close to existing defaults.

## Non-Goals
- Change tunnel or protocol behavior.
- Retune pump performance beyond the current defaults.
- Add or run automated tests.

## Affected Components
- `sfb/config.py`
- `sfb/cli.py`
- `sfb/channel/channel.py`
- `sfb/channel/channel_manager.py`
- `sfb/channel/control_channel.py`
- `sfb/modules/relay_connection.py`
- `sfb/modules/relay_pump.py`
- `sfb/modules/socks/socks_server.py`
- `sfb/modules/socks/socks_relay.py`
- `sfb/modules/port_fwd/port_fwd_server.py`
- `sfb/modules/port_fwd/port_fwd_relay.py`
- `scripts/icmp_socks_diag.py`
- `scripts/icmp_socks_test.py`
- `scripts/icmp_socks_scp_test.py`
- `doc/architecture/SOCKS.md` (if it references renamed knobs)

## Plan
1. Consolidate channel knobs.
   - Replace `channel_max_send_buf` and `channel_max_recv_buf` with a single
     `channel_max_buf` applied to both directions.
   - Remove `channel_write_backoff_initial`; derive initial backoff as
     `min(0.01, channel_write_backoff_max)`.
   - Remove `channel_control_read_chunk`; use a fixed constant inside
     `ControlChannel`.
   - Update config defaults, `_FIELDS`, validation, and all channel
     construction call sites.

2. Consolidate relay timeouts and ownership.
   - Remove `relay_channel_open_timeout` and use `channel_open_timeout` for
     relay modules; set `channel_open_timeout` default to 20s to preserve
     current relay behavior.
   - Remove `relay_socket_timeout` and use `relay_connect_timeout` for SOCKS
     handshake socket timeouts.
   - Remove `relay_target_connect_timeout` and use `relay_connect_timeout` for
     target connects in SOCKS/port_fwd relay modules.
   - Remove `relay_pump_poll_timeout` and `relay_channel_timeout`; use
     `non_blocking_poll_timeout` and pump backoff in relay_pump.
   - Remove `relay_thread_join_timeout` and use `module_shutdown_timeout` for
     relay thread joins.

3. Align SOCKS listen config and CLI.
   - Replace `relay_listen_host`/`relay_listen_port` with `socks_listen_addr`
     (`host:port`) in config.
   - Replace `--socks-host`/`--socks-port` with `--socks-listen`.
   - Update SOCKS server defaults to use `socks_listen_addr`.
   - Update SOCKS helper scripts to pass `--socks-listen`.

4. Update docs and hidden CLI tunables.
   - Update any doc references to removed/renamed knobs (if present).
   - Replace hidden `--channel-max-send-buf` with `--channel-max-buf`.

5. Manual verification.
   - Confirm CLI and scripts accept the new SOCKS listen flag.
   - Confirm relay pumps use backoff without new busy loops.

## Testing
- Do not run tests.

## Execution Notes (2026-01-12)
- Consolidated channel, relay, and SOCKS config knobs per plan; defaulted `channel_open_timeout` to 20s and derived channel write backoff initial delay from `channel_write_backoff_max`.
- Updated relay pumps to use `non_blocking_poll_timeout` and backoff, and aligned relay joins with `module_shutdown_timeout`.
- Switched SOCKS listen config/CLI to `socks_listen_addr`/`--socks-listen`, and updated helper scripts plus docs.
- Additional touchpoints: `sfb.py`, `integration_tests/test_inmemory_lossy_file_transfer.py`,
  `doc/bugs/slow_icmp_socks_throughput.md`.
- Tests not run (per plan).
