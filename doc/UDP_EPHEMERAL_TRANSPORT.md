# UDP Ephemeral Transport

## Overview

The UDP ephemeral transport uses one UDP socket per request. Alice (client)
creates a fresh UDP socket for each send, waits for a single response, then
closes the socket. Bob (server) listens on a single UDP socket and replies
once per request.

This transport preserves the Alice-initiated asymmetry: Alice polls, Bob only
responds to incoming requests.

```
Alice                                              Bob
  │                                                  │
  │  UDP request (one socket, one source port)       │
  │─────────────────────────────────────────────────▶│
  │                                                  │
  │  UDP response (single reply)                     │
  │◀─────────────────────────────────────────────────│
```

---

## Alice (Client) Behavior

- Each send uses a new UDP socket bound to an ephemeral source port.
- After sending, the socket stays open until a response arrives or the
  pending timeout expires.
- When the socket closes, its source port enters a cooldown window before
  it can be reused.
- Multiple in-flight requests are allowed up to `max_in_flight`.

---

## Bob (Server) Behavior

- A single UDP socket is bound to `udp_ephemeral_listen_addr`.
- Each request returns `(payload, responder)`.
- The responder enforces `send_mtu` and attaches `payload_cap` for tunnel
  logging.

---

## Configuration

Client settings:

- `udp_ephemeral_target` (`--target`)
- `udp_ephemeral_payload_mtu` (`--udp-ephemeral-mtu`)
- `udp_ephemeral_pending_timeout` (`--udp-ephemeral-pending-timeout`)
- `udp_ephemeral_source_port_reuse_minutes`
  (`--udp-ephemeral-source-port-reuse-minutes`)

Server settings:

- `udp_ephemeral_listen_addr` (`--listen-addr` or `--udp-ephemeral-listen-addr`)
- `udp_ephemeral_payload_mtu` (`--udp-ephemeral-mtu`)

Defaults:

- `udp_ephemeral_payload_mtu`: 1400
- `udp_ephemeral_pending_timeout`: 5.0
- `udp_ephemeral_source_port_reuse_minutes`: 1.0
- `udp_ephemeral_listen_addr`: 0.0.0.0:53

---

## MTU and Timeouts

The transport enforces `send_mtu` and `recv_mtu` on payload sizes. The
pending timeout controls how long Alice waits for a response before pruning
the request and closing its socket.
