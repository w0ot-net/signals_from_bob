# UDP Ephemeral Transport Plan

## Context
We need a new transport for a network where Alice can send exactly one UDP
message per socket and receive one reply, then must use a new UDP socket so
the source port changes. Alice must not reuse the same source port within N
minutes (default 1).

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
- `sfb/transport/udp_ephemeral/__init__.py`
- `sfb/transport/udp_ephemeral/udp_ephemeral_client.py`
- `sfb/transport/udp_ephemeral/udp_ephemeral_server.py`
- `sfb/transport/udp_ephemeral/udp_ephemeral_config.py` (if we centralize validation)
- `sfb/transport/__init__.py` (register transport)
- `sfb/config.py` (defaults and config fields)
- `sfb/cli.py` (CLI args and config wiring)
- `doc/TRANSPORTS.md` (transport list and description)
- `doc/UDP_EPHEMERAL_TRANSPORT.md` (new transport doc)
- `tests/test_udp_one_shot_transport.py` (if we add unit coverage)

## Proposed Design

### Transport Name
Transport name: `udp_ephemeral`.

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
- Allow multiple in-flight requests, bounded by `max_in_flight`.
- MTU:
  - Enforce `send_mtu` and `recv_mtu` on payload length.
No socket pool (skip for now).

### Server (Bob)
- Single UDP socket bound to `udp_listen_addr`.
- `recv()` reads a datagram and returns `(payload, responder)`.
- `responder()` sends a single UDP reply to the source addr and enforces
  `send_mtu`.
- Attach `payload_cap = send_mtu` to responder for tunnel logging.

### Configuration
Add UDP transport settings to `Config`, with CLI flags:
- `udp_ephemeral_target` (client target host:port).
- `udp_ephemeral_listen_addr` (server listen host:port).
- `udp_ephemeral_payload_mtu` (default chosen below).
- `udp_ephemeral_pending_timeout` (seconds).
- `udp_ephemeral_source_port_reuse_minutes` (float, default 1.0).

### Defaults
- `udp_ephemeral_payload_mtu`: 1200 (safe for UDP without fragmentation).
- `udp_ephemeral_source_port_reuse_minutes`: 1.0.

## Detailed Steps
1. Add Config fields and CLI wiring for client/server args.
2. Implement `UdpEphemeralClient` with:
   - Socket allocation with port cooldown enforcement.
   - Pending tracker, corr_id mapping, and recv select loop.
3. Implement `UdpEphemeralServer` with single socket and responder closure.
4. Register the transport in `sfb/transport/__init__.py`.
5. Add docs: `doc/UDP_EPHEMERAL_TRANSPORT.md` and update `doc/TRANSPORTS.md`.
6. Add focused unit tests if desired (no E2E runs).

## Test Plan
- Unit tests for:
  - One-shot send/recv over loopback.
  - Port reuse cooldown enforcement using `time_provider`.
  - Pending timeout cleanup closes sockets.
- Run with `python3 -m unittest` (no `tests/e2e/`).

## Execution Notes
- Added UDP ephemeral transport client/server with per-request sockets,
  pending tracking, and source port cooldown enforcement.
- Wired new UDP ephemeral config fields and CLI flags, plus transport registry
  registration.
- Documented the new transport and updated the transport overview.
- Tests not run (per instructions).
