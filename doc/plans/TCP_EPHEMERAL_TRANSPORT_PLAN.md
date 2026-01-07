# TCP Ephemeral Transport Plan

Status: draft

## Goal

Add a new `tcp_ephemeral` transport that uses one TCP connection per poll with
length-prefixed payloads, preserving the Alice-initiated asymmetry and MTU
negotiation rules.

## Non-Goals

- Add TLS, HTTP proxy, or other cover protocols.
- Add IPv6 support.
- Change tunnel reliability, keepalive, or retransmit behavior.

## Affected Components

- sfb/transport/tcp_ephemeral/__init__.py
- sfb/transport/tcp_ephemeral/tcp_ephemeral_client.py
- sfb/transport/tcp_ephemeral/tcp_ephemeral_server.py
- sfb/transport/tcp_ephemeral/tcp_ephemeral_config.py
- sfb/transport/__init__.py
- sfb/transport/mtu_limits.py
- sfb/config.py
- sfb/cli.py
- doc/architecture/TCP_EPHEMERAL_TRANSPORT.md
- doc/architecture/TRANSPORTS.md

## Design Notes

- Transport name: `tcp_ephemeral`.
- Each poll uses a fresh TCP connection; one request and one response per
  connection; Alice always initiates and Bob only responds.
- Framing: 2-byte big-endian length prefix followed by packet bytes; reject
  frames larger than `send_packet_mtu`/`recv_packet_mtu` and close on parse
  errors or early EOF.
- Client uses non-blocking connect with select-driven progress so multiple
  connections can be in flight (`max_in_flight`); server accepts non-blocking
  and reads until a full frame is available, then returns `(payload, responder)`.
- Timeouts use `time_provider.now()`:
  - `tcp_ephemeral_connect_timeout` for client connects.
  - `tcp_ephemeral_pending_timeout` for response wait and server request read.
- MTU: `tcp_ephemeral_packet_mtu` is a packet-byte cap; clamp to 65535 due to
  the 16-bit length prefix and enforce `MIN_PACKET_MTU`. Send/recv MTUs are
  symmetric at the transport level but still negotiated asymmetrically by the
  tunnel.
- Default `tcp_ephemeral_packet_mtu`: 1350 to bias toward single TCP segments
  on common 1500-MTU paths while remaining conservative.
- Keepalive/pong suppression remains handled by the tunnel; the transport
  carries packet bytes only and emits no extra traffic.
- Standard library only; Python 2.7/3 compatible; ASCII source code.

## Implementation Steps

1. Configuration and CLI plumbing.
   - Add `tcp_ephemeral_target`, `tcp_ephemeral_listen_addr`,
     `tcp_ephemeral_packet_mtu`, `tcp_ephemeral_pending_timeout`, and
     `tcp_ephemeral_connect_timeout` to `sfb/config.py` with validation.
   - Add CLI args (`--tcp-ephemeral-*`, `--target`, `--listen-addr`) and wire
     them into `create_config` in `sfb/cli.py`.
2. MTU resolution and transport registry.
   - Add a `tcp_ephemeral` branch in `sfb/transport/mtu_limits.py` to resolve
     MTUs from the new cap and minimums.
   - Register the new client/server classes in `sfb/transport/__init__.py`.
3. Transport implementation.
   - Add `tcp_ephemeral_config.py` validation helpers using `parse_host_port`
     and `TransportError`.
   - Client: non-blocking connect, per-connection state (phase, buffers,
     deadlines), length-prefixed send/recv, `PendingTracker` pruning, and
     structured logging.
   - Server: accept loop with max_in_flight gating, per-connection read buffer
     and expected length, responder that queues a length-prefixed response,
     write flush on readiness, timeout pruning, and structured logging.
4. Documentation.
   - Create `doc/architecture/TCP_EPHEMERAL_TRANSPORT.md` describing framing,
     client/server behavior, config defaults, MTU semantics, and TIME_WAIT
     connection churn considerations.
   - Update `doc/architecture/TRANSPORTS.md` with MTU table and correlation ID
     entry for `tcp_ephemeral`.

## Validation

- Manual loopback with python3 on localhost (client + server) to confirm
  request/response, timeouts, and MTU negotiation behavior.
- Do not run tests in tests/e2e/.
