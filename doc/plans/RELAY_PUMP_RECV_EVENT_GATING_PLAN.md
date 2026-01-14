# Relay Pump Recv Event Gating Plan

Status: draft

## Summary
Reduce lock and Event churn by switching channel receive readiness to a
level-triggered signal. The change introduces a monotonic recv-sequence signal
so callers only read when the signal advances, eliminating zero-timeout
polling loops and unnecessary Event clear/set traffic.

## Goals
- Reduce per-iteration lock/Event overhead in relay pumps without changing MTU.
- Lower CPU cost per byte while keeping throughput and latency stable.
- Preserve correctness for channel close and half-close handling.

## Non-Goals
- Change MTU negotiation or packet sizing.
- Modify protocol behavior or reliability semantics.
- Add or run automated tests.

## Affected Components
- `sfb/channel/channel.py`
- `sfb/channel/control_channel.py`
- `sfb/modules/relay_pump.py`
- `sfb/modules/nc_linux/nc_linux_pump.py`

## Plan
1. Add a recv-ready signal in `Channel`.
   - Provide a recv sequence counter that advances whenever recv data arrives
     or recv-close state changes.
   - Expose a helper that returns the current recv sequence without side
     effects.
   - Use level-triggered readiness so waiters can block until the sequence
     changes.

2. Update channel readers to use recv sequence gating (breaking change).
   - Replace zero-timeout read probes in `pump_channel_to_socket` with:
     cache recv sequence, wait on recv event, then read only when the sequence
     advances.
   - Apply the same pattern to other channel readers so they do not clear
     recv events without consuming data.

3. Preserve close detection.
   - Ensure recv sequence advances on close/half-close so waiters wake and
     can observe EOF promptly.

## Testing
- Do not run tests.
