# ICMP Transport

This document defines the plan and design for an ICMP Echo transport.
It targets Linux first. Windows support will be added later.
All code must remain Python 2.7/3 compatible and use only the standard library.

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
`doc/ASYMMETRY.md` and the tunnel behavior around retransmit and timeouts.
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

- Expose separate `send_mtu` and `recv_mtu` values.
- Default them to the same computed ICMP payload cap (symmetric in practice),
  while still allowing independent clamping during MTU negotiation.

Proposed approach:
- Add a config field (name TBD) for ICMP payload size limits, with a conservative
  default (for example 1350 bytes of ICMP payload to avoid fragmentation on 1500 MTU links).
- `send_mtu`/`recv_mtu` should reflect the SFB packet size carried in the ICMP
  data payload, not including ICMP headers. The tunnel already subtracts
  `PACKET_HEADER_SIZE`.

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

Proposed config fields (final names TBD):
- `icmp_target`: Alice target host/IP
- `icmp_payload_mtu`: max SFB packet size to send/receive (default conservative)
- `max_in_flight`: max concurrent ICMP requests in flight
- `icmp_pending_timeout`: timeout before pruning stale ICMP requests
- `tunnel_send_rate` / `tunnel_send_burst`: transport-agnostic pacing for Alice polls
- `tunnel_pace_target_inflight_ratio` / `tunnel_pace_min_inflight` /
  `tunnel_pace_max_inflight`: adaptive pacing bounds
- `tunnel_pace_feedback_gain` / `tunnel_pace_ack_ewma_alpha` /
  `tunnel_pace_rtt_floor_ms` / `tunnel_pace_ack_idle_reset_sec`:
  adaptive pacing feedback tuning

CLI:
- `--transport icmp`
- Alice: `--icmp-target <host>`
- Alice pacing (all transports): `--send-rate`, `--send-burst`,
  `--pace-target-inflight-ratio`, `--pace-min-inflight`,
  `--pace-max-inflight`, `--pace-feedback-gain`,
  `--pace-ack-ewma-alpha`, `--pace-rtt-floor-ms`,
  `--pace-ack-idle-reset-sec`
- Bob: likely no extra args beyond listen defaults

---

## Implementation Plan

1. Add `doc/ICMP_TRANSPORT.md` (this file).
2. Add `sfb/transport/icmp/icmp_client.py`:
   - raw ICMP socket
   - privilege check
   - checksum implementation
   - pending tracker (similar to DNS client)
   - `send()` constructs Echo Request with SFB payload (using a permit from
     `reserve_send()`)
   - `recv()` reads replies, validates type/framing, returns `(corr_id, data)`
3. Add `sfb/transport/icmp/icmp_server.py`:
   - raw ICMP socket
   - `recv()` reads Echo Requests, validates ICMP header/type
   - responder sends Echo Reply to request source address
4. Wire into `sfb/transport/__init__.py` and CLI transport selection.
5. Add config defaults and validation in `sfb/config.py`.
6. Add unit tests:
   - checksum correctness
   - send/recv path with fake sockets
   - non-blocking poll behavior (uses `non_blocking_poll_timeout`)
7. Document Windows follow-up work (ctypes + IcmpSendEcho).

---

## Testing Plan

Unit tests only. No e2e tests in `tests/e2e` will be run locally.

Test cases:
- ICMP checksum known vectors
- Encode/decode echo request/reply
- Pending tracker correlation
- Polling behavior does not busy loop
- Privilege check error path

---

## Future Work

- Windows support using ctypes and IcmpSendEcho or raw sockets where allowed.
- IPv6 ICMPv6 support.
- Optional path MTU discovery and dynamic MTU updates.
