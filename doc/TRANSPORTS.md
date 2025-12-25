# Transport Layer

## Overview

All transports use a **request/response** pattern. Alice (client) sends a
request containing her data, and Bob (server) responds with his data. This
design reflects the fundamental asymmetry of covert channels: Alice initiates
all communication, Bob can only respond.

---

## Transport Interfaces

### Client Side (Alice)

```python
class RequestResponseTransport:
    def exchange(self, data: bytes) -> bytes:
        """Send data to Bob, return his response."""
        ...

    @property
    def send_mtu(self) -> int:
        """Max bytes that can be sent in one exchange."""
        ...

    @property
    def recv_mtu(self) -> int:
        """Max bytes that can be received in one exchange."""
        ...

    def close(self):
        """Release resources."""
        ...
```

### Server Side (Bob)

```python
class RequestResponseServer:
    def recv(self, timeout=None) -> tuple:
        """
        Wait for request from Alice.

        Returns:
            (data, responder) - data is Alice's bytes, responder is
            a callable that takes bytes and sends the response.
            Returns (None, None) on timeout.
        """
        ...

    @property
    def send_mtu(self) -> int:
        """Max bytes that can be sent in one response."""
        ...

    @property
    def recv_mtu(self) -> int:
        """Max bytes that can be received in one request."""
        ...

    def close(self):
        """Release resources."""
        ...
```

---

## Pipelining

### Alice: Parallel Queries

For request/response transports, pipelining is transport-specific. A DNS
implementation can maintain multiple in-flight queries internally and map
responses back to requests. The reliability layer still handles out-of-order
responses by matching seq/ack numbers.

### Bob: Serial Processing

Bob processes one query at a time:

```python
while True:
    alice_data, responder = transport.recv()  # blocks until query arrives
    bob_data = process(alice_data)
    responder(bob_data)  # responds to that query
```

The transport internally tracks query/response pairing (e.g., DNS query ID).
Between `recv()` and `responder()`, the query context is held implicitly.
Bob must call `responder()` before the next `recv()`.

Serial processing is not a bottleneck - Bob's processing is microseconds of
crypto and buffer operations. The throughput limit is Alice's query rate.

### Network-Level Constraint

Bob cannot send without a pending query from Alice. This is the
fundamental asymmetry: Alice initiates all transport-level connections. At
the tunnel level, both sides can initiate operations - Bob just has latency
waiting for the next poll.

---

## DNS Transport

See `DNS_TRANSPORT.md` for complete specification.

### Overview

- Alice sends TXT/NULL queries to Bob (direct or via resolvers)
- Data encoded in subdomain labels with nonce prefix for cache busting
- Bob responds with TXT/NULL records containing data
- Supports EDNS0 for larger responses

### Query Format (Alice → Bob)

```
<nonce>.<data_labels>.<base_domain>
```

Example:
```
A7B3.JBSWY3DP.KNQWG5A.tunnel.example.com
```

- Nonce: 2-4 char unique prefix to prevent caching
- Data: base32 encoded (RFC 4648, no padding), split across 63-char labels
- Base domain: configured (shorter = higher MTU)

### Response Format (Bob → Alice)

TXT or NULL record containing base64-encoded packet.

```
TXT "SGVsbG8gV29ybGQ..."
```

For larger packets, multiple TXT strings are concatenated:

```
TXT "<first 255 chars>" "<next 255 chars>" ...
```

With EDNS0 (4096-byte UDP), responses can hold ~3KB of data.

### Operating Modes

| Mode | Description |
|------|-------------|
| Direct | Alice queries Bob directly (no domain setup needed) |
| Authoritative | Bob is NS for domain (works through resolvers) |

### Encoding

| Direction | Encoding | Overhead |
|-----------|----------|----------|
| Query (A→B) | Base32 | 1.625x |
| Response (B→A) | Base64 | 1.333x |

### Capacity

| Configuration | Query MTU | Response MTU |
|---------------|-----------|--------------|
| Standard TXT | ~138 bytes | ~191 bytes |
| EDNS0 + NULL | ~138 bytes | ~3038 bytes |

Query-side is the bottleneck. See `DNS_TRANSPORT.md` for MTU calculations.

### Bob's Outbound Buffer

Bob queues outgoing packets. On each query from Alice:

1. Parse incoming data (if any)
2. Check outbound queue
3. If data queued: respond with next packet
4. If no channel data is queued: respond with packet containing `{"cmd":"pong"}` on channel 0

### Example Flow (Serial)

```
Alice                                          Bob
  │                                              │
  │─ TXT? A1.<ping_pkt>.tunnel.example.com ────▶│  (nonce + packet)
  │◀── TXT "<pong_pkt>" ────────────────────────│  (nothing else queued)
  │                                              │
  │─ TXT? A2.<data_pkt>.tunnel.example.com ────▶│  (nonce + data packet)
  │◀── TXT "<response_pkt>" ────────────────────│  (Bob had data)
  │                                              │
```

All queries include a unique nonce prefix (A1, A2, ...) for cache busting.
Packets are base32-encoded in query labels, base64-encoded in TXT responses.

### Example Flow (Pipelined)

```
Alice                                          Bob
  │                                              │
  │─── TXT? A1.<pkt1>.tunnel.example.com ──────▶│
  │─── TXT? A2.<pkt2>.tunnel.example.com ──────▶│  (queries in flight)
  │─── TXT? A3.<pkt3>.tunnel.example.com ──────▶│
  │                                              │
  │◀── TXT "<bob_pkt1, ack=1>" ──────────────────│  (responses may be
  │◀── TXT "<bob_pkt2, ack=2>" ──────────────────│   out of order)
  │◀── TXT "<bob_pkt3, ack=3>" ──────────────────│
  │                                              │
```

Pipelining allows Alice to have up to `max_in_flight` (16) queries in flight.
Bob processes queries as they arrive and responds to each. The reliability
layer handles out-of-order responses via sequence numbers.

---

## ICMP Transport (Future)

### Overview

- Alice sends ICMP echo requests with data in payload
- Bob responds with echo replies containing response data

### Encoding

ICMP payload is raw bytes. No encoding needed, but:
- ICMP ID field: unused
- ICMP seq field: can map to tunnel seq or be independent
- Payload: encrypted tunnel packet

### Considerations

- Requires raw sockets (root/admin)
- Some networks filter ICMP
- Payload size varies by network, typically ~1400 bytes safe

---

## TLS Handshake Transport (Future Only)

### Overview

Abuse TLS handshake messages for covert data. This does not establish or use
TLS connections; only the handshake is used, then the connection is reset.
This transport is not part of the current implementation.

- Alice sends ClientHello with data in SNI or extensions
- Bob sends ServerHello with data in extensions
- Reset and repeat

### Encoding

Data hidden in:
- SNI (Server Name Indication): limited size
- Session ID: 32 bytes
- Extensions: variable

### Considerations

- Very limited bandwidth
- Looks like failed TLS connections
- Detection possible via pattern analysis

---

## Adding New Transports

1. Create `sfb/transport/new_transport.py`
2. Implement `RequestResponseTransport` (client) and/or `RequestResponseServer` (server)
3. Handle medium-specific encoding (e.g., base32 for DNS labels)
4. Provide `send_mtu` and `recv_mtu` properties

The transport is unaware of tunnel protocol—it just moves encrypted bytes
via the `exchange()` / `recv()`+`responder()` pattern.
