# ICMP Transport

This document defines the plan and design for an ICMP Echo transport.
It targets Linux first and includes a Windows backend in the plan.
All code must remain Python 2.7/3 compatible and use only the standard library.

---

## Goals

- Use ICMP Echo (type 8/0) for the request/response transport.
- Require root (or CAP_NET_RAW) to run; fail fast with a clear error.
- Preserve tunnel asymmetry rules (Alice initiates, Bob responds to polls).
- Preserve per-direction MTU negotiation (independent send/recv MTUs).
- Keep transport stateless: Bob accepts packets if the payload decodes
  as an SFB packet, and responds to the source address of that poll.
- Keep the design compatible with Linux and Windows backends.

## Non-Goals (for initial implementation)

- IPv6 support.
- Session tracking beyond the source address for the current request.

---

## Transport Overview

Alice sends ICMP Echo Requests carrying an SFB packet payload. Bob replies
with ICMP Echo Replies carrying the response SFB packet payload. This maps
cleanly to the existing request/response transport interface.

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
The transport itself does not generate keepalives or pongs.

---

## Privilege Check

Linux raw ICMP sockets require root (or CAP_NET_RAW). The transport should
check this in `__init__` and raise `TransportError` with a clear message if
privileges are insufficient.

Suggested check for Linux:
- `os.geteuid() != 0` -> error

---

## Addressing and Peer Identification

- Alice targets Bob by IPv4 address or hostname (resolve via `getaddrinfo`).
- Bob does not track sessions. If a packet payload decodes into a valid
  SFB packet, it is processed. Otherwise it is ignored.
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
- Add config fields (names TBD) for ICMP payload size limits, with a conservative
  default (for example 1200 bytes of ICMP payload to avoid fragmentation).
- `send_mtu`/`recv_mtu` should reflect the raw ICMP payload cap, not including
  tunnel packet overhead. The tunnel already subtracts `PACKET_HEADER_SIZE`.

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
  data=<SFB packet bytes>
```

Correlation IDs:
- Alice uses a monotonically increasing sequence number for each send.
- The sequence number is used as the transport correlation ID.
- Responses are matched by (id, seq) to the pending tracker.

---

## Polling Semantics

- Alice sends packets via `send()`, which emits an ICMP Echo Request.
- Bob receives via `recv()`, validates payload, and returns a responder that
  sends an Echo Reply to the same source address with the same id/seq.
- Alice polls via `recv(timeout=non_blocking_poll_timeout)` in its loop.
- Bob should use timeouts consistent with the tunnel poll intervals; when
  no packet is received, it returns `(None, None)` without busy looping.

Use `non_blocking_poll_timeout` in tight poll loops to avoid CPU spikes.

---

## Configuration and CLI

Proposed config fields (final names TBD):
- `icmp_target`: Alice target host/IP
- `icmp_payload_mtu`: max payload size to send/receive (default conservative)
- `icmp_recv_timeout`: socket timeout for recv
- `icmp_send_interval`: optional pacing for Alice sends

CLI:
- `--transport icmp`
- Alice: `--icmp_target <host>`
- Bob: likely no extra args beyond listen defaults

---

## Implementation Plan

1. Add `doc/ICMP_TRANSPORT.md` (this file).
2. Add `sfb/transport/icmp/icmp_client.py`:
   - raw ICMP socket
   - privilege check
   - checksum implementation
   - pending tracker (similar to DNS client)
   - `send()` constructs Echo Request with SFB payload
   - `recv()` reads replies, validates checksum/type, returns `(corr_id, data)`
3. Add `sfb/transport/icmp/icmp_server.py`:
   - raw ICMP socket
   - `recv()` reads Echo Requests, validates payload as SFB packet
   - responder sends Echo Reply to request source address
4. Wire into `sfb/transport/__init__.py` and CLI transport selection.
5. Add config defaults and validation in `sfb/config.py`.
6. Add unit tests:
   - checksum correctness
   - send/recv path with fake sockets
   - non-blocking poll behavior (uses `non_blocking_poll_timeout`)
7. Add Windows backend using ctypes (IcmpSendEcho) and OS switch.

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

- IPv6 ICMPv6 support.
- Optional path MTU discovery and dynamic MTU updates.
