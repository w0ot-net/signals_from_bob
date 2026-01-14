# DNS TXT Transport

This document describes the DNS TXT transport, which tunnels data using TXT
records for responses and base32-encoded QNAMEs for requests. The transport is
IPv4-only; IPv6 is unsupported.

---

## Overview

The DNS TXT transport uses the same asymmetric polling model as the DNS CNAME
transport:

- Alice encodes tunnel packets into DNS query names (QNAME).
- Bob decodes the QNAME and replies with a TXT answer containing data.
- Alice initiates all queries; Bob only responds to polls.

```
Alice                                           Bob
  │                                               │
  │  TXT? <nonce>.<data>.tunnel.example.com      │
  │──────────────────────────────────────────────▶│
  │                                               │
  │           TXT "<base64 payload>"             │
  │◀──────────────────────────────────────────────│
  │                                               │
```

---

## Operating Modes

The TXT transport supports direct and authoritative modes, identical to the
DNS transport:

### Direct Mode

- Alice queries Bob directly (set `dns_resolver` to Bob's host:port).
- No DNS delegation required.
- Common for local tests on UDP/5353.

### Authoritative Mode

- Bob is authoritative for `dns_base_domain` and listens on UDP/53.
- Alice uses the system resolver (omit `dns_resolver`).
- Works through recursive resolvers at the cost of extra latency.

---

## Query Format (Alice -> Bob)

Queries embed tunnel data in the QNAME using base32 labels plus a nonce:

```
<nonce>.<label_0>.<label_1>...<label_n>.<base_domain>
```

- `nonce` is 4 base32 characters derived from a 16-bit counter.
- `label_*` contain base32-encoded packet bytes.
- `base_domain` is `dns_base_domain` (lowercased, without a trailing dot).

The nonce prevents resolver caching and keeps repeated queries unique.

---

## Response Format (Bob -> Alice)

Bob replies with a TXT answer whose RDATA contains base64-encoded packet bytes.
TXT strings are split into 255-byte chunks per DNS TXT rules, each prefixed by
its length byte.

There are no CNAME follow-up queries in the TXT transport.

---

## MTU Calculation

MTU is asymmetric and expressed in tunnel packet bytes:

- Query MTU: `calc_query_mtu(base_domain, label_max_len)`
- Response MTU: `calc_response_mtu(QTYPE_TXT, dns_edns_size)`

EDNS0 OPT records are included when `dns_edns_size > 512`. The response packet
must fit within the configured EDNS size; oversized responses are rejected.
`dns_recv_bufsize_min` only affects the UDP socket buffer size, not the on-wire
limit.

---

## Configuration

The TXT transport reuses DNS config fields:

| Field | Description |
|-------|-------------|
| `dns_base_domain` | Base domain suffix for tunnel queries |
| `dns_resolver` | Resolver host:port for direct mode (client only) |
| `dns_listen_addr` | UDP listen host:port (server only) |
| `dns_label_max_len` | Max label length for base32 data |
| `dns_edns_size` | EDNS0 UDP payload size (OPT record) |
| `dns_recv_bufsize_min` | Minimum UDP recv buffer size |
| `dns_pending_timeout` | Pending query timeout (client) |
| `dns_response_ttl` | TXT answer TTL (server) |
| `max_in_flight` | Max in-flight requests |

The following DNS fields are ignored by the TXT transport:
`dns_query_type`, `dns_response_type`, `dns_cname_label`, `dns_cname_a_addr`.

To avoid caching, set `dns_response_ttl` to 0 for TXT responses.

---

## Error Handling

- Malformed queries or parse errors are ignored.
- Queries outside `dns_base_domain` are ignored.
- QTYPE mismatch or decode failures return an empty NOERROR response with a
  minimal SOA record (TTL=0) to avoid negative caching.

---

## Transport Interface

Client:

```python
from sfb.transport.dns_txt import DnsTxtClient

client = DnsTxtClient(config)
permit = client.reserve_send()
if permit is None:
    raise RuntimeError('capacity exhausted')
corr_id = client.send(packet, permit)
corr_id, response = client.recv(timeout=5.0)
```

Server:

```python
from sfb.transport.dns_txt import DnsTxtServer

server = DnsTxtServer(config)
while True:
    data, responder = server.recv(timeout=None)
    if data is None:
        continue
    responder(process(data))
```
