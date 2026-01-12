# ICMP Transport

This document describes the design and current implementation of the ICMP
Echo transport.
It targets Linux first. Windows support will be added later.
All code must remain Python 2.7/3 compatible and use only the standard library.
The transport is IPv4-only; IPv6 is unsupported.

---

## Goals

- Use ICMP Echo (type 8/0) for the request/response transport.
- Require root to run; fail fast with a clear error.
- Preserve tunnel asymmetry rules (Alice initiates, Bob responds to polls).
- Preserve per-direction MTU negotiation (independent send/recv MTUs).
- Keep transport stateless: Bob accepts ICMP Echo Requests with valid
  ICMP framing, passes the payload to the tunnel unchanged, and responds
  to the source address of that poll. ICMP checksums are set for wire
  compatibility but are not used for integrity.

## Non-Goals (for initial implementation)

- Windows support (planned follow-up).
- IPv6 support.
- Session tracking beyond the source address for the current request.

---

## Transport Overview

Alice sends ICMP Echo Requests carrying an SFB packet payload (header clear,
body possibly encrypted). Bob replies with ICMP Echo Replies carrying the
response SFB packet payload. This maps cleanly to the existing
request/response transport interface.

```
Alice                                       Bob
  │ ICMP Echo Request (SFB packet payload)   │
  ├─────────────────────────────────────────▶│
  │                                          │
  │ ICMP Echo Reply (SFB packet payload)     │
  ◀─────────────────────────────────────────┤
```

Bob only responds to polls (requests). This honors the asymmetry rules in
`doc/architecture/ASYMMETRY.md` and the tunnel behavior around retransmit and timeouts.
The transport itself does not generate keepalive-only packets; keepalive is
handled by the tunnel.

---

## Privilege Check

Linux raw ICMP sockets require root. The transport should check this in
`__init__` and raise `TransportError` with a clear message if privileges
are insufficient.

Suggested check for Linux:
- `os.geteuid() != 0` -> error (hard fail)

Bob must also disable kernel ICMP echo replies so the transport does not
compete with the kernel responder:
- `net.ipv4.icmp_echo_ignore_all` must be set to `1`
- Disable with: `sudo sysctl -w net.ipv4.icmp_echo_ignore_all=1`

---

## Addressing and Peer Identification

- Alice targets Bob by IPv4 address or hostname (resolve via `getaddrinfo`).
- Bob does not track sessions. If a packet is a valid ICMP Echo Request
  for this transport, its payload is passed to the tunnel as opaque bytes.
  Otherwise it is ignored.
- Bob responds to the source address of the received request. No long-lived
  session mapping is required.

This implies that multiple senders could poll Bob; Bob will simply respond
to whichever valid poll arrived.

---

## MTU Strategy

Even though ICMP is not constrained like DNS, the tunnel requires independent
send/recv MTUs per side. The ICMP transport should:

- Expose separate `send_packet_mtu` and `recv_packet_mtu` values.
- Default them to the same computed ICMP payload cap (symmetric in practice),
  while still allowing independent clamping during MTU negotiation.
- Treat MTUs as packet bytes (header + segments) before ICMP framing.
- Enforce a minimum packet MTU of
  `PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1`.

Current approach:
- `icmp_packet_mtu` is a cap for auto-selected ICMP payload size, with a
  conservative default (1350 bytes to avoid fragmentation on 1500 MTU links).
- Auto selection clamps to the cap even if a larger payload is possible.
  Increasing the cap raises fragmentation risk on public paths.
- `send_packet_mtu`/`recv_packet_mtu` reflect the SFB packet size carried in
  the ICMP data payload (ICMP + IPv4 headers are extra on the wire).
  The tunnel derives payload bytes by subtracting `PACKET_HEADER_SIZE`.

If future path MTU discovery is added, it should update these independently.

---

## ICMP Packet Format

ICMP header fields (type, code, checksum, id, seq) must be set correctly.
The payload is the raw SFB packet bytes (encrypted by the tunnel layer).

Payload layout (conceptual):
```
ICMP Echo:
  type=8 (request) or 0 (reply)
  code=0
  checksum=ICMP checksum
  id=<transport identifier>
  seq=<per-request sequence>
  data=<SFB packet bytes (header clear, body possibly encrypted)>
```

Correlation IDs:
- Alice uses a random 16-bit Echo ID per transport instance.
- Alice uses a monotonically increasing sequence number for each send.
- The sequence number is used as the transport correlation ID.
- Responses are matched by (id, seq) to the pending tracker.

---

## Polling Semantics

- Alice reserves capacity via `reserve_send()` then calls `send()`, which
  emits an ICMP Echo Request.
- Bob receives via `recv()`, validates ICMP framing/type, and returns a
  responder that sends an Echo Reply to the same source address with the
  same id/seq. The payload is passed to the tunnel unchanged. Checksum
  validation is optional and disabled by default.
- Alice polls via `recv(timeout=non_blocking_poll_timeout)` in its loop.
- Bob should use timeouts consistent with the tunnel poll intervals; when
  no packet is received, it returns `(None, None)` without busy looping.

Use `non_blocking_poll_timeout` in tight poll loops to avoid CPU spikes.

---

## Configuration and CLI

Config fields:
- `icmp_target`: Alice target host/IP
- `icmp_packet_mtu`: max SFB packet size to send/receive (default conservative)
- `max_in_flight`: max concurrent ICMP requests in flight
- `icmp_pending_timeout`: timeout before pruning stale ICMP requests
- `icmp_socket_rcvbuf`: optional ICMP socket receive buffer size (bytes, 0 = default)
- `icmp_socket_sndbuf`: optional ICMP socket send buffer size (bytes, 0 = default)
- `non_blocking_poll_timeout`: poll timeout used by the tunnel loop
- `tunnel_send_rate` / `tunnel_send_burst`: transport-agnostic pacing for Alice polls
- `tunnel_pace_target_inflight_ratio` / `tunnel_pace_min_inflight` /
  `tunnel_pace_max_inflight`: adaptive pacing bounds
- `tunnel_pace_feedback_gain` / `tunnel_pace_ack_ewma_alpha` /
  `tunnel_pace_rtt_floor_ms` / `tunnel_pace_ack_idle_reset_sec`:
  adaptive pacing feedback tuning

CLI:
- `--transport icmp`
- Alice: `--target <host>`
- `--icmp-packet-mtu <bytes>` (both roles)
- Alice pacing (all transports): `--send-rate`, `--send-burst`,
  `--pace-target-inflight-ratio`, `--pace-min-inflight`,
  `--pace-max-inflight`, `--pace-feedback-gain`,
  `--pace-ack-ewma-alpha`, `--pace-rtt-floor-ms`,
  `--pace-ack-idle-reset-sec`
- Bob: `--icmp-packet-mtu <bytes>` only

---

## Logging and Performance

ICMP per-packet logging is expensive. For production throughput, keep
`log_component_transport_icmp` disabled or use `log_event_whitelist` to
limit ICMP events. The default blacklist suppresses `icmp.send` and
`icmp.recv`.

---

## Implementation

- `sfb/transport/icmp/icmp_client.py`: raw ICMP socket, privilege check,
  pending tracker, Echo Request send, Echo Reply recv; checksum validation is
  disabled by default.
- `sfb/transport/icmp/icmp_server.py`: raw ICMP socket, privilege check,
  kernel echo suppression check, Echo Request recv, Echo Reply send.
- `sfb/transport/icmp/icmp_packet.py`: Echo packet build/parse and checksum.
- Wired into `sfb/transport/__init__.py`, CLI selection, and config validation.

---

## Tests

Unit tests only. No e2e tests in `tests/e2e` will be run locally.

- `tests/test_icmp_packet.py` (checksum, encode/decode, parse errors)
- `tests/test_icmp_client.py` (pending correlation, error paths)
- `tests/test_icmp_server.py` (request parsing, responder errors)

---

## Future Work

- Windows support using ctypes and IcmpSendEcho or raw sockets where allowed.
- IPv6 ICMPv6 support.
- Optional path MTU discovery and dynamic MTU updates.
