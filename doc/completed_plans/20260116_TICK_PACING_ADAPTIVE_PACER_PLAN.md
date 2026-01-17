# Tick Pacing Adaptive Pacer Plan

## Goal
Make Alice's adaptive pacer influence the tick loop rate, so tick pacing
targets the same inflight goal used for send gating, and add minimal
instrumentation to confirm the pacing loop behavior in logs.

## Non-goals
- Change Bob behavior or transport semantics.
- Alter packet formats or protocol invariants.
- Add new CLI knobs unless required for correctness.
- Touch tests (per instructions).

## Affected Components
- sfb/tunnel/alice_tunnel.py
- sfb/reliability/pacing.py
- sfb/reliability/pacer_logging.py
- sfb/config.py (only if a new pacing knob is required)
- sfb/cli.py (only if exposing a new knob becomes necessary)

## Plan
1) Map current pacing flow:
   - Identify where `_poll_pacing_interval`, `_sleep_for_poll_pacing`, and
     `_tick_sleep_hint` are computed.
   - Confirm how `_poll_pacing_interval` uses the adaptive pacer target and
     how often `_advance_poll_pacing` is called.
2) Define the tick pacing policy:
   - Use the adaptive pacer target inflight to derive a tick delay that
     matches `compute_poll_pacing_interval`.
   - Keep the invariant that tick pacing never exceeds the keepalive interval
     and never drops below the configured minimum.
3) Surface pacer gating for tick pacing:
   - Have `_can_send_new` return pacer block details so tick can distinguish
     pacer gating from other send blocks.
   - Thread block reason and target inflight through `_send_pending_or_poll`
     to `tick()` so a pacing interval/next-allowed time can be derived.
4) Wire tick pacing into `tick()`:
   - When the pacer blocks or when polls are gated, sleep for the computed
     interval instead of the current fixed `tunnel_tick_sleep`/0.01 fallback.
   - Keep tick pacing state separate from `_next_poll_time` so poll gating
     remains based on polls, not tick sleeps.
   - Ensure the sleep uses `time_provider.sleep` and respects
     `_poll_pace_sleep_max` bounds to avoid long sleeps.
   - Normalize `_tick_sleep_hint` so the outer run loop doesn't double-sleep
     (set to 0.0 when tick already slept; otherwise carry the pacing-aware
     hint for run/_run_loop).
5) Add minimal instrumentation:
   - Log a new debug event (or reuse `tunnel.poll_pace`) that records the
     computed tick delay, target inflight, and whether the tick slept for
     pacing vs idle.
   - Keep logs off by default; only emit at DEBUG.
6) Remove redundant logic:
   - If tick pacing replaces the old idle sleep branch, collapse duplicated
     sleep code paths into a single pacing-aware decision.
   - Ensure the new control path does not increase branching or state unless
     strictly necessary.

## Validation
- In `logs/client_log.db`, confirm new tick pacing logs show stable intervals
  that track the pacer target inflight.
- Verify `tunnel.send_blocked` counts drop and inflight oscillation narrows
  compared to the current logs.
- Confirm no change in protocol errors or decode failures.

## Risks
- Aggressive tick sleeps could under-drive polling and reduce throughput if
  the interval is too large for current RTT.
- Overly tight tick pacing could reintroduce oscillation if it conflicts with
  send gating; keep the pacing interval derived from the same pacer target to
  avoid dual-control conflicts.

## Execution Notes
- Propagated pacer block reasons through the send gate to drive tick pacing.
- Added tick pacing scheduling separate from poll pacing state with debug
  `tunnel.tick_pace` sleep logging.
- No tests run (per instructions; e2e tests deferred to user).
