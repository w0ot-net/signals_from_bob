# ICMP Transport Performance Plan

Status: draft

## Summary
Split ICMP performance work into three phases covering client hot paths,
server hot paths, and operational tuning. Note that Bob's ICMP throughput is
fundamentally bounded by Alice's poll rate per doc/architecture/ASYMMETRY.md,
so server-side tweaks are incremental.

## Goals
- Reduce syscall and allocation overhead in ICMP client and server hot paths.
- Reuse receive buffers where safe on Python 3 without breaking Python 2.
- Preserve ICMP transport semantics, logging, and error handling.

## Non-Goals
- Change asymmetry behavior, retransmit strategy, or polling semantics.
- Add new dependencies or run automated tests.
- Modify non-ICMP transports.

## Affected Components
- `sfb/transport/icmp/icmp_client.py`
- `sfb/transport/icmp/icmp_server.py`
- `sfb/transport/icmp/icmp_packet.py` (if buffer-view parsing is needed)
- `sfb/compat.py` (only if a new helper is required)
- `sfb/config.py` (only if ICMP socket buffer sizing becomes configurable)
- `doc/architecture/ICMP_TRANSPORT.md` (if config or behavior notes change)

## Phases
- Phase 1 (completed): ICMP client hot path and allocation work.
  See `doc/completed_plans/20260111_ICMP_TRANSPORT_PERF_PHASE1.md`.
- Phase 2: ICMP server hot path and allocation work.
  See `doc/plans/ICMP_TRANSPORT_PERF_PHASE2.md`.
- Phase 3: ICMP socket buffer sizing and logging guidance.
  See `doc/plans/ICMP_TRANSPORT_PERF_PHASE3.md`.

## Helper Notes
- Bob throughput is bounded by Alice polling; improvements should not assume
  server-side receive throughput can exceed the poll rate.
- Avoid list/dict/set comprehensions in `sfb/` for Python 2 flat builds.
- Per-packet logging is expensive; keep ICMP logging disabled or whitelist
  events in production runs.

## Plan
1. Review the current ICMP client/server hot paths and confirm all changes
   stay within the asymmetry rules in doc/architecture/ASYMMETRY.md.
2. Execute Phase 2, then Phase 3 (phases are independent but ordered by risk
   and scope).

## Testing
- Do not run tests.
