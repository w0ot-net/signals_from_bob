# UDP Ephemeral Transport

## Overview

The UDP ephemeral transport uses one UDP socket per request. Alice (client)
creates a fresh UDP socket for each send, waits for a single response, then
closes the socket. Bob (server) listens on a single UDP socket and replies
once per request.

The transport is IPv4-only; IPv6 is unsupported.

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
- The responder enforces `send_packet_mtu` on responses.

---

## Configuration

Client settings:

- `udp_ephemeral_target` (`--target`)
- `udp_ephemeral_packet_mtu` (`--udp-ephemeral-packet-mtu`)
- `udp_ephemeral_pending_timeout` (`--udp-ephemeral-pending-timeout`)
- `udp_ephemeral_source_port_reuse_minutes`
  (`--udp-ephemeral-source-port-reuse-minutes`)

Server settings:

- `udp_ephemeral_listen_addr` (`--listen-addr`)
- `udp_ephemeral_packet_mtu` (`--udp-ephemeral-packet-mtu`)

Defaults:

- `udp_ephemeral_packet_mtu`: 1350
- `udp_ephemeral_pending_timeout`: 5.0
- `udp_ephemeral_source_port_reuse_minutes`: 1.0
- `udp_ephemeral_listen_addr`: 0.0.0.0:53

---

## MTU and Timeouts

The transport enforces `send_packet_mtu` and `recv_packet_mtu` on packet sizes.
These are packet bytes (header + segments) before UDP framing; on-wire UDP and
IPv4 headers add 28 bytes.

MTU behavior:
- `udp_ephemeral_packet_mtu` is a cap for auto-selected UDP payload size.
- Defaults to 1350 (safe on typical 1500 MTU Internet paths).
- Auto selection clamps to the cap even if a larger payload is possible.
- Larger caps increase fragmentation risk.
- Minimum packet MTU is `PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1`.

Payload bytes are derived as `(packet_mtu - PACKET_HEADER_SIZE)`. The pending
timeout controls how long Alice waits for a response before pruning the
request and closing its socket.
