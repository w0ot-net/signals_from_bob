# Logging Control Plan

## Goals
- Reduce log spam on both stdout and SQLite.
- Keep CLI flags as coarse controls (level, handler enablement).
- Add fine-grained, config-only toggles per component.
- Preserve Python 2.7/3 compatibility and standard library only.

## Scope
- Implement component-based filtering for both stdout and SQLite.
- Use a logging.Filter that classifies records by event prefix or logger name.
- Start with transport/dns, then move to tunnel/channel, modules, and protocol.

## Proposed Component Map (initial)
- transport.dns
  - events: dns.*
  - loggers: sfb.transport.dns.*, tunnel.sfb.transport.dns.*
- transport.icmp
  - loggers: sfb.transport.icmp.*
- tunnel
  - events: tunnel.*
  - loggers: sfb.tunnel.*, tunnel.sfb.tunnel.*
- channel
  - events: channel.*
  - loggers: sfb.channel.*
- module.socks
  - events: sock.*
  - loggers: sfb.modules.socks.*
- module.file_transfer
  - loggers: sfb.modules.file_transfer.*
- protocol
  - loggers: sfb.protocol.*
- cli
  - loggers: sfb.cli, sfb

## Proposed Defaults (pending confirmation)
- on: tunnel, module.socks, module.file_transfer, cli
- off: transport.dns, transport.icmp, channel, protocol

## Implementation Plan
1) Config
   - Add config fields for component toggles (single set applied to both stdout and SQLite).
2) Logging filter
   - Implement a filter that maps each record to a component and allows/denies.
   - Use event prefix when present; fallback to logger name prefix.
3) Wiring
   - Attach the filter to stdout and SQLite handlers after config is created.
4) Docs
   - Update logging docs with new config options and component map.
5) Consistency pass
   - Standardize logger naming on the sfb.* namespace.
   - Avoid ad-hoc logger names in modules and services.
6) Event filters
   - Add structured event whitelist/blacklist patterns.
   - Default blacklist suppresses high-volume debug events.
7) Structured conversion
   - Convert high-volume unstructured logs to structured events.

## Progress
- [x] Phase 1: transport/dns component filter
- [x] Phase 2: tunnel/channel component filter
- [x] Phase 3: modules component filter
- [x] Phase 4: protocol and misc loggers
- [x] Consistency pass: logger naming
- [x] Event whitelist/blacklist filter
- [x] Structured conversion: base_tunnel debug logs
- [x] Structured conversion: dns/protocol debug logs
- [x] Structured conversion: cli/modules logs

## Execution Notes
- Completed prior to this change per user report; no additional execution steps
  were run as part of this move.
