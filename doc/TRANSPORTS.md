# Transport Layer

## Overview

All transports use a **request/response** pattern at the wire level. Alice
(client) sends requests, Bob (server) responds. This reflects the fundamental
asymmetry of covert channels: Alice initiates all communication.

The transport interface separates `send()` and `recv()` to support pipelining -
multiple requests in flight simultaneously. For serial operation, simply call
`recv()` after each `send()`, or set `max_pending=1`.

---

## Transport Interface

### Client Side (Alice)

```python
class Transport:
    """
    Client transport with pipelining support.

    Separates send/recv to allow multiple in-flight requests.
    Correlation IDs match responses to requests.
    """

    def send(self, data: bytes) -> int:
        """
        Send data to Bob.

        Args:
            data: Packet bytes to send

        Returns:
            Correlation ID for matching response
        """
        ...

    def recv(self, timeout: float = None) -> tuple:
        """
        Receive next available response.

        Args:
            timeout: Max seconds to wait
                     None = block until response
                     0 = non-blocking poll

        Returns:
            (correlation_id, data) on success
            (None, None) on timeout
        """
        ...

    def pending_count(self) -> int:
        """Number of requests awaiting response."""
        ...

    @property
    def max_pending(self) -> int:
        """Max concurrent in-flight requests (transport limit)."""
        ...

    @property
    def send_mtu(self) -> int:
        """Max bytes per send."""
        ...

    @property
    def recv_mtu(self) -> int:
        """Max bytes per recv."""
        ...

    def close(self):
        """Release resources, cancel pending requests."""
        ...
```

### Server Side (Bob)

```python
class Server:
    """
    Server transport for request/response.

    Bob receives requests and must respond to each before
    calling recv() again.
    """

    def recv(self, timeout: float = None) -> tuple:
        """
        Wait for request from Alice.

        Args:
            timeout: Max seconds to wait (None = block)

        Returns:
            (data, responder) where responder is a callable
            (None, None) on timeout
        """
        ...

    @property
    def send_mtu(self) -> int:
        """Max bytes per response."""
        ...

    @property
    def recv_mtu(self) -> int:
        """Max bytes per request."""
        ...

    def close(self):
        """Release resources."""
        ...
```

---

## Usage Patterns

### Serial (max_pending=1 or explicit)

```python
# Equivalent to old exchange() - one request, wait for response
corr_id = transport.send(packet_data)
corr_id, response_data = transport.recv(timeout=5.0)
```

### Pipelined

```python
# Alice's main loop
def tick():
    # Receive all available responses (non-blocking)
    while True:
        corr_id, response = transport.recv(timeout=0)
        if corr_id is None:
            break
        process_response(corr_id, response)

    # Send new packets up to limit
    while can_send_more():
        corr_id = transport.send(next_packet())
        track_in_flight(corr_id)

def can_send_more():
    return (transport.pending_count() < transport.max_pending and
            tunnel.send_window.can_send)
```

### Effective In-Flight Limit

The actual in-flight count is bounded by:

```python
effective_limit = min(
    transport.max_pending,      # Transport capacity (e.g., 16)
    tunnel.negotiated_window,   # Tunnel negotiated limit
)
```

---

## Correlation IDs

The correlation ID returned by `send()` is opaque to the tunnel layer. The
transport uses it internally to match responses:

| Transport | Correlation Strategy |
|-----------|---------------------|
| DNS | Maps to DNS query ID (16-bit) |
| ICMP | Maps to ICMP sequence number |
| HTTP | Maps to request context |
| In-memory | Incrementing integer per transport instance |

The tunnel tracks `{corr_id: (seq, send_time, is_retransmit)}` for:
- RTT calculation (only from first transmissions)
- Timeout detection
- Response processing

---

## Bob: Serial Processing

Bob processes one request at a time:

```python
while True:
    data, responder = server.recv(timeout=1.0)
    if data is None:
        check_idle_timeout()
        continue

    response = process(data)
    responder(response)
```

The server tracks request/response pairing internally (e.g., DNS query ID).
Bob must call `responder()` before the next `recv()`.

Serial processing is not a bottleneck - Bob's processing is microseconds of
crypto and buffer operations. Throughput is limited by Alice's send rate.

---

## DNS Transport

See `DNS_TRANSPORT.md` for complete specification.

### Overview

- Alice sends A queries to Bob (direct or via resolvers)
- Data encoded in subdomain labels with nonce prefix
- Bob responds with CNAME records containing data
- `max_pending` controls concurrent queries (default: 16)

### Query Format (Alice → Bob)

```
<nonce>.<data_labels>.<base_domain>
```

Example:
```
A7B3.JBSWY3DP.KNQWG5A.tunnel.example.com
```

### Response Format (Bob → Alice)

CNAME record with base32 data encoded into the target name:

```
CNAME <data_labels>.<cname_label>.<base_domain>
```

### Example Flow (Pipelined)

```
Alice                                          Bob
  │                                              │
  │─── A? A1.<pkt1>.tunnel.example.com ────────▶│
  │─── A? A2.<pkt2>.tunnel.example.com ────────▶│  (queries in flight)
  │─── A? A3.<pkt3>.tunnel.example.com ────────▶│
  │                                              │
  │◀── CNAME "<bob_pkt1, ack=1>" ───────────────│  (responses arrive,
  │◀── CNAME "<bob_pkt3, ack=3>" ───────────────│   possibly reordered)
  │◀── CNAME "<bob_pkt2, ack=2>" ───────────────│
  │                                              │
```

Alice matches responses to requests via correlation ID (mapped to DNS query
ID). The reliability layer handles out-of-order via sequence numbers.

### Serial Mode

For serial DNS (one query at a time):

```python
transport = DnsTransport(resolver, domain, max_pending=1)
```

---

## ICMP Transport

### Overview

- Alice sends ICMP Echo Requests with data in payload
- Bob responds with Echo Replies containing response data
- `max_pending` controls concurrent requests

### Correlation

- Random ICMP ID per transport instance + sequence number maps to correlation ID
- Responses matched by (id, seq)

### Considerations

- Requires raw sockets (root on Linux)
- Kernel echo replies must be disabled (net.ipv4.icmp_echo_ignore_all=1)
- Some networks filter ICMP
- Payload size varies, typically ~1200 bytes safe on 1500 MTU links

---

## In-Memory Transport (Testing)

- For local/unit testing with no network dependency
- Backed by in-process queues; MTU defaults to `DEFAULT_MAX_PACKET_SIZE`
- Max pending defaults to `tunnel_max_in_flight` unless overridden
- Use `create_inmemory_transport_pair(Config())` to get a connected
  `(Transport, Server)` pair for tests or simulations

---

## Adding New Transports

1. Create `sfb/transport/new_transport.py`
2. Implement `Transport` (client) and/or `Server`
3. Handle medium-specific encoding
4. Implement correlation ID mapping for `send()`/`recv()`
5. Set appropriate `max_pending` for the medium

The transport is unaware of tunnel protocol - it just moves encrypted bytes.
