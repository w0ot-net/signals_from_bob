# SOCKS Performance Plan

## Goal
Reduce relay stalls, CPU spin, and idle latency in the SOCKS data pumps while
preserving protocol behavior and cross platform support.

## Issues
- A single socket timeout applies to both recv and sendall, so slow receivers
  can raise socket.timeout during sendall and tear down an otherwise healthy
  relay.
- Backpressure is implemented via sleep and poll loops when the channel send
  buffer is full, which wastes CPU as connection counts rise.
- After channel.read timeouts, the pump sleeps again, adding extra idle
  latency.

## Constraints
- Python 2.7 and 3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in doc/ASYMMETRY.md.
- Keepalive pongs remain suppressed while any channel has pending data.
- Do not run E2E tests under tests/e2e/ (user only).

## Plan
1. Baseline and reproduce.
   - Use existing socks logging profiles to capture buffer_full, sleep_time,
     and relay error rates under load.
   - Record CPU usage and throughput before changes for comparison.
   - Specify the harness (script, ports, concurrency, duration) so baselines
     are repeatable.
2. Decouple recv timeouts from sendall.
   - Keep sockets in blocking mode with no timeout.
   - Replace recv timeouts with select.select (or equivalent) on readability
     so we can poll with a timeout without touching the socket timeout.
   - Ensure stop_event is honored on timeout paths.
   - Avoid indefinite blocking on sendall by selecting for writability with a
     bounded timeout or using a non-blocking send loop that checks stop_event.
3. Add event driven backpressure for channel send buffers.
   - Extend Channel with a send buffer state event or wait method that
     unblocks when send buffer space is available.
   - Use the existing send state transition callback to signal the event.
   - In pump_socket_to_channel, wait on the event when the send buffer is
     full instead of sleeping with exponential backoff.
   - Wait in a loop on actual buffer state and wake on channel close or
     stop_event to avoid missed signals and deadlocks.
4. Remove redundant idle sleeps in pump_channel_to_socket.
   - When channel.read returns None (timeout), loop without an extra sleep so
     the existing timeout drives the polling cadence.
   - If channel.read can return immediately (closed or zero timeout), add a
     small bounded wait or guard to prevent spin.
5. Add targeted unit tests.
   - Channel send buffer wait method: blocks until buffer drains and respects
     timeouts.
   - Data pump: relay continues under backpressure without socket.timeout
     (use local loopback sockets for Windows compatibility).
   - Data pump: stop_event terminates a blocked send path in bounded time
     (use select on writable to keep Windows compatibility).
   - Run python3 -m unittest for the new tests (no E2E tests).

## Acceptance Criteria
- No socket.timeout exceptions on sendall under backpressure.
- CPU usage does not spike with many idle or backpressured connections.
- Throughput and latency are equal or better than baseline.
- New unit tests pass on Windows and Linux with python3.
