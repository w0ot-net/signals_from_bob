# Plan: Gate Stats Collection Behind -v

## Goals
- Collect runtime stats only when verbose logging (-v) is enabled.
- Reduce hot-path overhead from counter updates and snapshots when not verbose.
- Keep behavior unchanged when -v is set; maintain Python 2.7/3 and Windows/Linux support.

## Non-Goals
- Changing transport/reliability behavior or protocol semantics.
- Removing log event names; only skip stats fields and periodic stats events when not verbose.
- Running E2E tests.

## Affected Components
- sfb/cli.py
- sfb/config.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/reliability/stats.py
- sfb/reliability/send_window.py
- sfb/reliability/recv_window.py
- sfb/channel/channel_manager.py
- sfb/modules/relay_pump.py
- sfb/modules/nc_linux/nc_linux_pump.py
- sfb/modules/file_transfer/file_transfer.py
- sfb/transport/lossy.py
- doc/ALICE_RETRANSMIT_LOGIC.md
- doc/RELIABILITY.md
- doc/LOGGING.md
- doc/LOSSY_TRANSPORT.md
- doc/CHANNEL_MANAGER.md
- tests/test_tunnel.py
- tests/test_send_window.py
- tests/test_reliability.py
- tests/test_lossy_transport.py

## Design Notes
- Add a single config flag (config.stats_enabled or config.verbose) derived from args.verbose.
- Replace tunnel_stats_enabled with the new flag (no parallel knobs).
- Stats disabled means: no counter increments, no snapshots, no per-interval pump stats,
  and no stats fields in stop events or stdout summaries.

## Plan
1) Config and CLI wiring
   - Add config.stats_enabled: bool = False.
   - Set config.stats_enabled = args.verbose in create_config or main.
   - Remove tunnel_stats_enabled and update references in code/docs/tests.
2) Reliability stats gating
   - BaseTunnel creates ReliabilityStats only when stats_enabled, otherwise NoopReliabilityStats.
   - Skip stat_ fields in tunnel packet logs when stats disabled.
3) Channel drain stats
   - Guard ChannelManager._record_drain_stats with stats_enabled in addition to logging.DEBUG.
4) Pump stats (relay + nc_linux)
   - Add a stats_enabled flag in pump functions; when false, skip per-second counters and pump_stats events.
   - When stats disabled, pump_stop should omit stats dicts entirely.
5) File transfer stats
   - Only create TransferStats when stats_enabled.
   - Only emit stats fields and stdout summaries when stats exist.
6) Lossy transport stats
   - Add stats_enabled to impairment engines; skip counter increments when disabled.
   - Decide on stats() behavior (empty dict or zeros) and document it.
7) Docs and tests
   - Update docs to state stats collection requires -v.
   - Update unit tests that assert stats to enable stats explicitly (no tests/e2e).

## Validation
- Run targeted unit tests for reliability, tunnel, and lossy stats (no tests/e2e).

## Execution Notes
- Wired config.stats_enabled to -v and removed tunnel_stats_enabled usage.
- Gated drain stats, pump stats, file transfer stats, and lossy transport stats.
- Updated docs and stats-related tests to require explicit stats enablement.
- Validation: `python3 -m unittest tests.test_tunnel tests.test_reliability tests.test_lossy_transport` (fails: test_collect_segments_keepalive_only_when_idle unexpected keepalive_data arg; test_process_incoming_packet_orders_control_before_data Channel._deliver read-only).
