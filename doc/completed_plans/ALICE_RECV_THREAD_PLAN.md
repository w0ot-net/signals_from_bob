# Alice Receive Thread Plan

## Background
Alice currently drains responses in `tick()` by repeatedly calling
`transport.recv()` with a very small timeout and an extra 50ms wait when
pending is high. This can cause either busy polling (CPU) or added latency
when the 50ms wait is hit. The goal is to avoid polling without changing
protocol behavior.

## Goals
- Remove polling from the main tick loop.
- Keep latency low for incoming responses.
- Preserve current send/retransmit behavior and asymmetry rules.
- Use only the Python standard library and keep Python 2.7/3 compatibility.

## Non-Goals
- No protocol or packet format changes.
- No transport behavior changes on the wire.
- No changes to Bob-side behavior.

## Decision
- This change is deferred; we are not going to implement it right now.
- Idle CPU usage is already low, so the primary benefit (idle CPU reduction)
  is negligible.
- Throughput would not materially change because `max_pending` and Alice's
  polling rate still dominate.
- The remaining benefit is mostly reduced tail latency from the 50ms
  high-pending wait and the 1ms tick sleep, and that is not currently a
  pain point.
- A receive thread adds complexity and risk (transport thread-safety,
  handshake ordering, shutdown races) without a clear payoff.
- If we revisit this later, the wake event must integrate with the run loop;
  otherwise the gains are limited.

## Proposed Design
- Add a dedicated receive thread in `AliceTunnel` that blocks on
  `transport.recv(timeout=None)` and pushes responses onto a thread-safe
  queue.
- The tick loop drains the queue at the start of each tick and processes
  responses using the existing `_handle_response` logic.
- An event is used to wake the tick loop (or to skip extra sleeping) when
  new responses arrive.

### Data Flow
- Receive thread:
  - Loop while a stop flag is not set.
  - Call `transport.recv(timeout=None)` to block for the next response.
  - On `(corr_id, data)`:
    - If `corr_id is None`, continue (timeout or spurious wake).
    - Enqueue `(corr_id, data, recv_time)` and set a wake event.
  - On `TransportError`, log and request tunnel close.
- Tick loop:
  - Drain all queued responses and run `_handle_response` for each.
  - Maintain `received_any`, `received_valid`, and `last_resp_has_data` with
    the same semantics as today.
  - Remove the direct `transport.recv()` polling and the fixed 50ms wait.

### Thread Lifecycle
- Start the receive thread when Alice enters `CONNECTED` state (or during
  tunnel start).
- On `close()`:
  - Set a stop flag.
  - Close the transport to unblock any blocking `recv()`.
  - Join the receive thread with a timeout.
- Ensure the thread handles exceptions and exits cleanly.

## API and Internal Changes
- `AliceTunnel`:
  - Add `_recv_thread`, `_recv_stop`, `_recv_queue`, `_recv_event`.
  - Add `_start_recv_thread()` and `_stop_recv_thread()` helpers.
  - Update `tick()` to drain `_recv_queue` instead of calling
    `transport.recv()` directly.
- `Transport`:
  - No interface changes. All existing transports already implement
    `recv(timeout=None)`.

## Transport Behavior Notes
- DNS and ICMP clients use `select()` and sockets. Closing the socket should
  unblock a blocking `recv()`.
- In-memory transport uses a queue and blocks in `get()`. Closing the link
  should push a sentinel or otherwise unblock the queue.
- Lossy transport wraps the inner transport and should work unchanged.

## Concurrency and Ordering
- Responses are processed in the order they arrive at the receive thread.
- The queue preserves ordering. No additional reordering is introduced.
- Response processing still occurs on the main thread to avoid sharing
  tunnel state across threads.

## Risks
- Transport close may not always unblock `recv()` quickly; ensure a timeout
  or sentinel path for the in-memory transport if needed.
- Thread lifecycle errors could leak threads or keep the tunnel alive
  longer than expected.
- If the queue grows without bounds, memory could spike under extreme
  burst loads. Consider a reasonable cap with backpressure if needed.

## Validation Plan
- Unit tests for:
  - Receive thread starts and stops cleanly.
  - Responses are delivered to `_handle_response` in order.
  - Close unblocks the receive thread.
- Manual tests only; do not run e2e tests (per instructions).
