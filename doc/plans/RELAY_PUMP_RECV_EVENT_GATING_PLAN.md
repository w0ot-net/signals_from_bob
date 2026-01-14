# Relay Pump Recv Event Gating Plan

Status: draft

## Summary
Reduce lock and Event churn in relay pumps by avoiding zero-timeout channel
reads when the channel has no data ready. The change gates non-blocking reads
on a cheap recv-ready signal so we skip unnecessary `channel.read()` calls
while outbound data is pending.

## Goals
- Reduce per-iteration lock/Event overhead in relay pumps without changing MTU.
- Lower CPU cost per byte while keeping throughput and latency stable.
- Preserve correctness for channel close and half-close handling.

## Non-Goals
- Change MTU negotiation or packet sizing.
- Modify protocol behavior or reliability semantics.
- Add or run automated tests.

## Affected Components
- `sfb/modules/relay_pump.py`
- `sfb/channel/channel.py`

## Plan
1. Add a lightweight recv-ready check in `Channel`.
   - Provide a small helper (for example, `recv_ready()` or `has_recv_data()`)
     that returns True when there is buffered data or when the recv event is
     set due to new data or close.
   - Keep the helper cheap and thread-safe; prefer using existing state to
     avoid extra lock churn.

2. Gate zero-timeout reads in `pump_channel_to_socket`.
   - In the path where `read_timeout` is set to `0.0` because outbound data is
     pending, call `channel.read()` only if the recv-ready helper indicates
     data or close is ready.
   - Skip the read attempt when not ready, allowing the loop to focus on
     flushing outbound data without extra lock/Event traffic.

3. Preserve close detection.
   - Ensure the gating logic still allows channel close/half-close to be
     detected promptly by treating recv-close signals as ready in the helper.

## Testing
- Do not run tests.
