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
   - Record CPU usage, throughput, and idle latency before changes for
     comparison on Windows and Linux.
   - Use python3 for any harness scripts to match project rules.
   - Specify the harness (script, ports, concurrency, duration, payload sizes)
     so baselines are repeatable, and document target deltas to detect
     regressions.
2. Decouple recv timeouts from sendall.
   - Use non-blocking sockets and select.select to wait for readability and
     writability with bounded timeouts.
   - Confirm the pumps are the sole owners of socket I/O after setup; if not,
     document where non-blocking is set and how other callers handle it.
   - Replace recv timeouts with select on readability, then recv in a loop
     until EWOULDBLOCK or no data, so we never touch socket timeouts.
   - For sends, track a per-socket outbound buffer and partial sends; use a
     non-blocking send loop gated by select on writability, with a short
     timeout (100-250ms) to honor stop_event promptly.
   - Only include sockets in the writable set when outbound buffer has data to
     avoid select spin on Windows.
   - If a pump handles multiple sockets, cap per-iteration bytes or loops so
     a single busy socket cannot starve others; otherwise document the
     per-connection assumption.
   - Bound the per-socket outbound buffer and gate channel.read so pending data
     cannot grow unbounded when the socket is not writable (use a cap and
     low-water mark or read only when writable).
   - Handle EWOULDBLOCK/WSAEWOULDBLOCK consistently on Windows and Linux in
     both pumps.
   - Clarify how socks_relay_socket_timeout and socks_relay_write_timeout are
     used or retired once non-blocking/select is in place; update config/CLI
     documentation accordingly.
   - Document the chosen timeout values and verify they do not regress CPU.
3. Add event driven backpressure for channel send buffers.
   - Extend Channel with a send buffer space event or wait method that
     unblocks when the buffer transitions from full to not-full (level-triggered).
   - Use the existing send state transition callback only if it can signal
     buffer full/not-full transitions; otherwise add a dedicated signal.
   - In pump_socket_to_channel, wait on the event when the send buffer is
     full instead of sleeping with exponential backoff.
   - Define socket->channel behavior under backpressure: stop reading from the
     socket while the channel buffer is full, or buffer locally with a cap and
     low-water mark.
   - Wait in a loop on actual buffer state and wake on channel close or
     stop_event to avoid missed signals and deadlocks.
4. Remove redundant idle sleeps in pump_channel_to_socket.
   - When channel.read returns None (timeout), loop without an extra sleep so
     the existing timeout drives the polling cadence.
   - If channel.read can return immediately (closed or zero timeout), add a
     small bounded wait or guard to prevent spin, aligned with the select
     timeout used by the socket side.
5. Add targeted unit tests.
   - Channel send buffer wait method: blocks until buffer drains and respects
     timeouts.
   - Data pump: relay continues under backpressure without socket.timeout
     (use local loopback sockets for Windows compatibility).
   - Data pump: stop_event terminates a blocked send path in bounded time
     (use select on writable to keep Windows compatibility).
   - Keepalive suppression: verify no pong when any channel has pending data,
     even under backpressure.
   - Run python3 -m unittest for the new tests (no E2E tests).

## Acceptance Criteria
- No send-path timeouts or exceptions under backpressure, and no data loss
  during partial sends.
- No unbounded growth in per-socket or channel buffers.
- CPU usage does not spike with many idle or backpressured connections.
- Throughput and latency are equal or better than baseline.
- New unit tests pass on Windows and Linux with python3.
