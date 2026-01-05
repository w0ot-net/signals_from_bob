# UDP Ephemeral Socket Precreate Plan

## Goal
- Improve UDP ephemeral client throughput by pre-creating connected UDP sockets
  in small batches to reduce per-send setup overhead.
- Keep Alice-initiated asymmetry intact (Alice polls, Bob only responds).
- Preserve Python 2.7/3 compatibility and Windows/Linux support.

## Non-Goals
- Reuse sockets after a response (still one socket per request).
- Change Bob server behavior or add server-side pooling.
- Change the on-wire protocol or add transport-level acknowledgments.

## Affected Components
- doc/UDP_EPHEMERAL_TRANSPORT.md
- doc/TRANSPORTS.md
- sfb/config.py
- sfb/cli.py
- sfb/transport/udp_ephemeral/udp_ephemeral_config.py
- sfb/transport/udp_ephemeral/udp_ephemeral_client.py
- tests/test_udp_ephemeral_client.py

## Plan
1. Config and CLI
   - Add `udp_ephemeral_precreate_pool_max` (int, default 0 = disabled) to
     `Config`.
   - Add `udp_ephemeral_precreate_batch` (int, default 1) to control how many
     sockets to create per top-up when the pool is below the target.
   - Add CLI flags:
     - `--udp-ephemeral-precreate-pool-max`
     - `--udp-ephemeral-precreate-batch`
   - Validate both values in `validate_udp_ephemeral_config`, returning them
     in the normalized config.

2. Client precreate pool
   - Add an idle pool of connected sockets `(sock, local_port)`.
   - Add `_precreate_pool_max` and `_precreate_batch` to the client.
   - Implement `_fill_precreate_pool(now)` to create up to
     `min(precreate_batch, precreate_pool_max - len(pool))` sockets at once.
   - Call `_fill_precreate_pool()` when the pool drops below the target.
   - When sending, prefer a socket from the pool; fall back to a fresh socket
     if the pool is empty.
   - Keep the existing source-port cooldown logic in `_create_socket()` to
     avoid reusing ports too quickly.

3. Socket lifecycle
   - Precreated sockets remain single-use; once sent, they flow through the
     existing pending tracker and are closed on response or timeout.
   - Prune invalid precreated sockets before use.
   - Close unused precreated sockets on `close()` without recording port use
     to avoid unnecessary cooldown entries.

4. Logging
   - Log when the pool is topped up and when sockets are consumed from it.
   - Log when invalid precreated sockets are pruned.

5. Docs
   - Update `doc/UDP_EPHEMERAL_TRANSPORT.md` to describe the optional
     precreate pool and the new config flags.
   - Update `doc/TRANSPORTS.md` to note that UDP ephemeral still uses one
     socket per request, but sockets may be pre-created to reduce latency.

6. Tests (unit-only, no E2E)
   - Cover pool disabled (default) vs enabled behavior.
   - Ensure batch precreate fills up to the pool cap.
   - Ensure sockets from the pool are consumed before creating new ones.
