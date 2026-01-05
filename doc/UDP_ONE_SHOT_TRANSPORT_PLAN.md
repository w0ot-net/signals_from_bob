# UDP One-Shot Transport Plan

## Context
We need a new transport for a network where Alice can send exactly one UDP
message per socket and receive one reply, then must use a new UDP socket so
the source port changes. Alice must not reuse the same source port within N
minutes (default 1). A socket pool may help performance.

## Goals
- Add a new UDP transport that enforces one request/one response per socket.
- Enforce per-source-port reuse cooldown (minutes, default 1).
- Keep Python 2.7/3 compatibility, stdlib-only, Windows and Linux support.
- Preserve the Alice-initiated, Bob-responds-only asymmetry.
- Keep MTU negotiation asymmetric via transport send_mtu/recv_mtu.

## Non-Goals
- NAT traversal or hole punching.
- Changing tunnel retransmit/timeout logic.
- E2E tests (user will run them).

## Affected Components
- `sfb/transport/udp_one_shot/__init__.py`
- `sfb/transport/udp_one_shot/udp_one_shot_client.py`
- `sfb/transport/udp_one_shot/udp_one_shot_server.py`
- `sfb/transport/udp_one_shot/udp_one_shot_config.py` (if we centralize validation)
- `sfb/transport/__init__.py` (register transport)
- `sfb/config.py` (defaults and config fields)
- `sfb/cli.py` (CLI args and config wiring)
- `doc/TRANSPORTS.md` (transport list and description)
- `doc/UDP_ONE_SHOT_TRANSPORT.md` (new transport doc)
- `tests/test_udp_one_shot_transport.py` (if we add unit coverage)

## Proposed Design

### Transport Name
Tentative name: `udp_one_shot` (open to change).

### Client (Alice)
- Each send uses a fresh UDP socket bound to an ephemeral port.
- After send, the socket is kept only until its response arrives or it times out,
  then it is closed and its source port is recorded as recently used.
- Enforce port reuse cooldown:
  - Track `port_last_used` with `time_provider.now()`.
  - When creating a socket, bind to `('', 0)`, read the assigned port, and
    reject it if it was used within the cooldown window.
  - Retry a bounded number of attempts; if no eligible port is found, raise a
    TransportError (or optionally block until the earliest port expires).
- Pending tracking:
  - Use `PendingTracker` to prune timed-out requests.
  - Map corr_id -> state (socket, send_time, local_port).
  - `recv()` uses `select.select()` on the pending sockets, reads the first
    ready response, closes the socket, and returns `(corr_id, data)`.
- MTU:
  - Enforce `send_mtu` and `recv_mtu` on payload length.
- Optional socket pool:
  - Configurable `udp_socket_pool_size`.
  - Pre-create sockets that satisfy the cooldown rule.
  - `reserve_send()` pulls from the pool when available; after use, close and
    replenish the pool on demand.

### Server (Bob)
- Single UDP socket bound to `udp_listen_addr`.
- `recv()` reads a datagram and returns `(payload, responder)`.
- `responder()` sends a single UDP reply to the source addr and enforces
  `send_mtu`.
- Attach `payload_cap = send_mtu` to responder for tunnel logging.

### Configuration
Add UDP transport settings to `Config`, with CLI flags:
- `udp_target` (client target host:port).
- `udp_listen_addr` (server listen host:port).
- `udp_payload_mtu` (default chosen below).
- `udp_pending_timeout` (seconds).
- `udp_source_port_reuse_minutes` (float, default 1.0).
- `udp_socket_pool_size` (int, default 0 or 1).

### Defaults
- `udp_payload_mtu`: propose 1200 (safe for UDP without fragmentation), unless
  you prefer a different default.
- `udp_socket_pool_size`: default 0 (no pool) unless you want eager pooling.

## Detailed Steps
1. Decide transport name and config defaults.
2. Add Config fields and CLI wiring for client/server args.
3. Implement `UdpOneShotClient` with:
   - Socket allocation with port cooldown enforcement.
   - Pending tracker, corr_id mapping, and recv select loop.
   - Optional socket pool.
4. Implement `UdpOneShotServer` with single socket and responder closure.
5. Register the transport in `sfb/transport/__init__.py`.
6. Add docs: `doc/UDP_ONE_SHOT_TRANSPORT.md` and update `doc/TRANSPORTS.md`.
7. Add focused unit tests if desired (no E2E runs).

## Test Plan
- Unit tests for:
  - One-shot send/recv over loopback.
  - Port reuse cooldown enforcement using `time_provider`.
  - Pending timeout cleanup closes sockets.
- Run with `python3 -m unittest` (no `tests/e2e/`).

## Open Questions
- Transport name: `udp_one_shot` vs `udp_single_use` or another?
- Should we allow multiple in-flight sockets, or force `max_in_flight=1`?
- Preferred default `udp_payload_mtu` (1200 vs 1472)?
- If no eligible source port is available, should we block until reuse window
  expires or fail fast with a TransportError?
- Do you want a nonzero default socket pool size?
