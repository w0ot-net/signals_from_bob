# DNS Client (Alice)

This document describes the DNS client transport that Alice uses to send
tunnel packets as DNS queries and decode responses from Bob. It focuses on the
client-side control flow and limits; see `doc/architecture/DNS_TRANSPORT.md`
for on-wire DNS encoding details.

---

## Overview

- Transport: UDP/IPv4 to a resolver (direct to Bob or via a recursive resolver).
- Role: Alice initiates all queries; Bob can only respond to polls.
- Interface: `Transport` reserve/send/recv with pipelining support.

```
Alice (DnsClient)                             Resolver/Bob
  |-- DNS query (QNAME carries packet) ----->|
  |<-- DNS response (CNAME carries packet) --|
```

Pipelining allows multiple DNS queries in flight at once; responses may arrive
out of order and are matched by correlation ID.

---

## Responsibilities

- Encode tunnel packet bytes into DNS query names with a per-query nonce.
- Decode DNS CNAME responses back into tunnel packet bytes.
- Enforce send/receive MTU limits and `max_in_flight` concurrency.
- Track in-flight queries and prune stale ones with a monotonic timeout.
- Select a resolver (explicit or system) and manage the UDP socket lifecycle.

---

## Resolver Selection and Modes

`dns_resolver` controls how the client chooses a resolver:

- If set, parse `host:port` (default port 53) and send queries there.
  - Direct mode points this at Bob; direct tests typically use port 5353.
- If unset, load system resolvers:
  - Unix: `/etc/resolv.conf`
  - Windows: parse `nslookup` output

`dns_base_domain` is normalized to lowercase with the trailing dot stripped.
The transport only supports `dns_query_type = A` and
`dns_response_type = CNAME` (validated in config).

---

## MTU Selection (Asymmetric)

MTU limits are derived via `resolve_mtu_limits('dns', role='client')`:

- `send_packet_mtu` is the maximum tunnel packet size for a DNS query.
- `recv_packet_mtu` is the maximum tunnel packet size expected in a response.
- The two values can differ (asymmetric negotiation).

For CNAME responses, the client computes a fixed response payload cap using:

- raw query packet MTU
- EDNS size (controls UDP payload size)
- CNAME suffix and label length limits
- OPT record size

If the fixed cap is smaller than the negotiated receive MTU, the client clamps
`recv_packet_mtu` to the fixed cap and logs the clamp. This prevents building
responses that cannot fit within DNS size limits.

The UDP receive buffer size is `max(dns_edns_size, dns_recv_bufsize_min)` and
an EDNS0 OPT record is included when `dns_edns_size > 512`.

---

## Send Pipeline

1. `reserve_send()` prunes stale pending queries, checks `max_in_flight`, and
   returns a `SendPermit` when capacity is available.
2. `_send_impl()`:
   - Validates payload size against `send_packet_mtu`.
   - Allocates a correlation ID and a 16-bit DNS ID (both monotonic with
     randomized starts).
   - Encodes the query name with a per-query nonce and the base domain.
   - Builds a DNS query packet (RD flag, one question, optional OPT record).
   - Sends the packet via UDP and records the pending query by DNS ID.

Each pending entry stores the DNS ID and lowercased QNAME, and the client keeps
a 65,536-entry map from DNS ID to correlation ID for fast lookups.

---

## Receive Pipeline

`recv(timeout)` uses `select` on the non-blocking UDP socket:

- `timeout=None` blocks indefinitely.
- `timeout=0` performs a non-blocking poll.
- Any positive timeout waits up to that duration.

`_try_recv()` reads one datagram, parses it, and returns the matching payload
when valid:

- Rejects non-responses, RCODE errors, malformed questions/answers, and
  mismatched QNAMEs.
- Requires an IN-class answer of the configured response type.
- Decodes the CNAME target to tunnel bytes using the configured suffix and
  label length.
- Drops stale or unknown DNS IDs.

Error responses remove pending entries to avoid exhausting the in-flight
window, then return `(None, None)` so the caller can retry later.

---

## Pending Timeout and Pruning

In-flight queries are tracked by `PendingTracker` using
`dns_pending_timeout` and the monotonic clock (`time_provider.now()`):

- `reserve_send()` and `recv()` prune stale entries.
- When pruning, the DNS ID mapping is cleared so late responses are ignored.

---

## Interaction With the Tunnel Layer

The DNS client is strictly request/response and does not implement
retransmission or keepalive logic. The tunnel and reliability layers:

- Control when polls are sent (Alice initiates).
- Retransmit based on Alice RTT measurements.
- Suppress keepalive pongs when any channel has pending data.
- Bound Bob's throughput by Alice's polling cadence.

See `doc/architecture/ASYMMETRY.md` and `doc/architecture/RELIABILITY.md` for
the higher-level protocol behavior.

---

## Logging

Key log events emitted by the client:

- `dns.client_config`, `dns.fixed_response_cap`, `dns.mtu_clamp`
- `dns.send`, `dns.recv`
- `dns.malformed_response`, `dns.error_response`, `dns.prune_stale`

These logs include resolver details, payload sizes, and pruning counts to aid
diagnostics.
