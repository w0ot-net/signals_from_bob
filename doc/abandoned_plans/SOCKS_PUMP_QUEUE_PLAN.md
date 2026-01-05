# SOCKS Pump Queue Plan

## Summary
Replace the channel->socket pump with a reader/writer split that uses a
bounded byte queue. The reader blocks on channel reads, the writer blocks on
queue reads and only calls select() when it has data to send. This removes the
tight polling loops while keeping Windows/Linux compatibility and preserving
backpressure.

## Goals
- Remove tight polling/backoff in the channel->socket pump while idle or
  backpressured.
- Preserve bounded memory and backpressure by limiting queued bytes.
- Keep behavior consistent on Windows and Linux with Python 2.7/3 support.
- Retain existing stop semantics, half-close handling, and structured logging.

## Non-Goals
- Rewriting the socket->channel pump (beyond minimal coordination changes).
- Changing tunnel transport, MTU, or retransmission behavior.
- Running E2E tests (user runs those).

## Affected Components
- sfb/modules/socks/data_pump.py (byte queue, reader/writer threads, logging)
- sfb/modules/socks/relay_connection.py (no new API, but verify stop flow)
- sfb/config.py (optional queue sizing knobs if needed)
- sfb/cli.py (optional flags for new queue sizing knobs)
- doc/SOCKS.md (document new pump design and limits)
- doc/LOGGING.md (update pump stats fields if they change)

## Plan
1. Define a bounded byte queue helper in `sfb/modules/socks/data_pump.py`:
   - Backed by deque + size counter + threading.Condition.
   - `put(data)` blocks while size >= max_bytes; wakes on `get()` or close.
   - `get()` blocks while empty; returns (data, closed) or raises on closed.
   - `close()` wakes all waiters; used to signal EOF and shutdown.
   - Track current queue size for stats.
   - Add chunk coalescing on enqueue: if the last chunk + new data stays under
     a `coalesce_max` threshold (likely `socks_relay_buffer_size`), append to
     reduce per-item overhead.
   - Add a `max_items` cap (e.g., 2x `max_bytes / socks_relay_buffer_size`) to
     avoid unbounded chunk counts when upstream sends many tiny fragments.
2. Rework `pump_channel_to_socket` into a coordinator:
   - Start a reader thread that blocks on `channel.read(read_size, timeout=None)`.
   - On data, `put()` into the queue (blocks when full).
   - On EOF (`b''`), call `queue.close()` and exit.
   - On channel read error, set stop_event, close queue, and exit.
3. Implement a writer loop (in the main pump thread) that:
   - Blocks on `queue.get()` to obtain the next chunk.
   - Uses non-blocking `sock.send()` with select() only when a chunk is present.
   - Handles partial sends via offset tracking.
   - On socket error, set stop_event, close the channel (to unblock reader),
     close queue, and exit.
4. Preserve shutdown/half-close behavior:
   - After reader hits EOF and the queue drains, call `_shutdown_socket_write`.
   - If channel is fully closed, exit with the existing stop reason.
5. Adjust stats and logging:
   - Track queue size/limit, bytes read/sent, and writer select wait time.
   - Keep event names stable; add new fields only if needed.
6. Optional configuration:
   - If queue sizing needs a knob, add `socks_relay_outbound_max_bytes`.
   - Default to `_outbound_cap(config)` to preserve current limits.
7. Validate (non-E2E only):
   - Run targeted unit tests, if any, for SOCKS pump behavior.
   - Manual sanity: run a SOCKS transfer and verify CPU idle drop and logs.

## Validation
- Run non-E2E tests relevant to SOCKS or channel behavior (user runs E2E).
- Verify pump logs show stable bytes in/out and queue sizes without tight
  polling backoff.

## Abandonment notes
- 2025-09-19: Abandoned per request; no implementation work recorded.
