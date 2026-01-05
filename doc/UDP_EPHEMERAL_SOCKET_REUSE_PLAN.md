# UDP Ephemeral Socket Reuse Plan

## Goal
- Improve UDP ephemeral client throughput by reusing connected UDP sockets
  while still enforcing the existing source-port cooldown.
- Keep Alice-initiated asymmetry intact (Alice polls, Bob only responds).
- Preserve Python 2.7/3 compatibility and Windows/Linux support.

## Non-Goals
- Change Bob server behavior or add server-side socket reuse.
- Allow multiple in-flight requests on a single UDP socket.
- Change the on-wire protocol or add transport-level acknowledgments.

## Affected Components
- doc/UDP_EPHEMERAL_TRANSPORT.md
- doc/TRANSPORTS.md
- sfb/config.py
- sfb/cli.py
- sfb/transport/udp_ephemeral/udp_ephemeral_config.py
- sfb/transport/udp_ephemeral/udp_ephemeral_client.py
- tests/test_udp_ephemeral_client.py (new)

## Plan
1. Config and CLI
   - Add `udp_ephemeral_reuse_sockets` (bool, default True) to `Config`.
   - Add CLI flags:
     - `--udp-ephemeral-reuse-sockets`
     - `--udp-ephemeral-no-reuse-sockets`
   - Validate the flag in `validate_udp_ephemeral_config` and return it in
     the normalized config.

2. Client socket pooling
   - Add an idle socket pool (socket, local_port, last_used).
   - Reuse an idle socket only when `now - last_used >= reuse_seconds`.
   - Keep existing behavior when reuse is disabled (close after response,
     track port reuse cooldown).
   - Cap idle pool size (use `max_in_flight` as the default cap) and close
     oldest entries beyond the cap, recording port-use time as today.

3. Late packet safety
   - Before reusing a socket, drain any queued datagrams (non-blocking recv)
     and discard them, logging a debug event if anything is flushed.
   - This avoids mis-associating delayed responses with a new request.

4. Logging
   - Add a log event when a socket is reused vs newly created.
   - Add a log event when idle sockets are closed due to pool cap or invalid
     sockets are pruned.

5. Docs
   - Update `doc/UDP_EPHEMERAL_TRANSPORT.md` to describe the reuse mode,
     cooldown behavior, and the new config flag.
   - Update `doc/TRANSPORTS.md` to remove the "fresh socket per request"
     wording when reuse is enabled.

6. Tests (unit-only, no E2E)
   - Cover reuse enabled vs disabled behavior.
   - Ensure reuse is blocked until the cooldown passes.
   - Ensure pool cap closes excess sockets and records port usage.
