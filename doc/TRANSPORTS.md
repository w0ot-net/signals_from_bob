# Transport Layer

## Overview

All transports use a **request/response** pattern at the wire level. Alice
(client) sends requests, Bob (server) responds. This reflects the fundamental
asymmetry of covert channels: Alice initiates all communication.
All transports are IPv4-only; IPv6 addresses and sockets are not supported.

The transport interface separates reservation, `send()`, and `recv()` to
support pipelining - multiple requests in flight simultaneously. For serial
operation, reserve one permit, call `send()`, then `recv()`, or set
`max_in_flight=1`.

The tunnel passes wire packet bytes to the transport: the header is in cleartext
and the body may be encrypted depending on the configured cipher.

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

    def reserve_send(self, now=None):
        """
        Prune pending entries, check capacity, and reserve a send permit.

        Returns None when capacity is exhausted.
        """
        ...

    def send(self, data, permit):
        """
        Send data to Bob using a reserved permit.

        Args:
            data: Packet bytes to send
            permit: SendPermit from reserve_send()

        Returns:
            Correlation ID for matching response
        """
        ...

    def recv(self, timeout=None):
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

    def pending_count(self):
        """Number of requests awaiting response (non-pruning)."""
        ...

    @property
    def send_packet_mtu(self):
        """Max packet bytes per send."""
        ...

    @property
    def recv_packet_mtu(self):
        """Max packet bytes per recv."""
        ...

    def payload_cap_for_send(self, permit):
        """
        Optional per-send packet cap for tunnel payload collection.

        Returns packet byte cap or None.
        """
        ...

    def notify_send_pending(self, has_pending_data):
        """Optional hint about Alice pending data state."""
        ...

    def notify_recv_window_sack(self, sack):
        """Optional hint about Alice receive window SACK state."""
        ...

    def release_send(self, permit):
        """Release a reserved permit when a send is skipped."""
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

    def recv(self, timeout=None):
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
    def send_packet_mtu(self):
        """Max packet bytes per response."""
        ...

    @property
    def recv_packet_mtu(self):
        """Max packet bytes per request."""
        ...

    def close(self):
        """Release resources."""
        ...
```

The responder may expose `response_payload_cap` (packet bytes) to provide a
per-request response cap for Bob. This is distinct from the client-side
`payload_cap_for_send` hook.

---

## Usage Patterns

### Serial (max_in_flight=1 or explicit)

```python
# Equivalent to old exchange() - one request, wait for response
permit = transport.reserve_send()
if permit is None:
    raise RuntimeError('capacity exhausted')
corr_id = transport.send(packet_data, permit)
corr_id, response_data = transport.recv(timeout=5.0)
```

### Pipelined

```python
config = Config()

# Alice's main loop
def tick():
    # Receive all available responses (non-blocking)
    while True:
        corr_id, response = transport.recv(timeout=0)
        if corr_id is None:
            break
        process_response(corr_id, response)

    # Send new packets up to limit
    while tunnel.send_window.can_send:
        packet = next_packet()
        if packet is None:
            break
        permit = transport.reserve_send()
        if permit is None:
            break
        corr_id = transport.send(packet, permit)
        track_in_flight(corr_id)
```

### Effective In-Flight Limit

The in-flight count is bounded by the negotiated tunnel window. Transports
also cap their pending requests using the configured `max_in_flight`,
so there is no separate transport-specific limit.

---

## Correlation IDs

The correlation ID returned by `send()` is opaque to the tunnel layer. The
transport uses it internally to match responses:

| Transport | Correlation Strategy |
|-----------|---------------------|
| DNS | Maps to DNS query ID (16-bit) |
| ICMP | Maps to ICMP sequence number |
| UDP Ephemeral | Incrementing integer per request |
| TLS ClientHello | Maps to per-connection correlation ID |
| TLS Handshake Bump | Maps to per-connection correlation ID |
| HTTP | Maps to request context |
| In-memory | Incrementing integer per transport instance |
| Lossy (wrapper) | Wrapper IDs map to inner corr_id(s) per send |

The tunnel tracks `{corr_id: (seq, send_time, is_retransmit)}` for:
- RTT calculation (only from first transmissions)
- Timeout detection
- Response processing

For lossy wrappers, the wrapper correlation ID is independent of the inner
transport. Duplicate sends can map multiple inner IDs to the same wrapper ID,
and pending counts include synthetic drops, delayed sends, and duplicates.

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
- `max_in_flight` controls concurrent queries

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
config = Config(max_in_flight=1)
transport = DnsTransport(resolver, domain, config=config)
```

---

## ICMP Transport

### Overview

- Alice sends ICMP Echo Requests with data in payload
- Bob responds with Echo Replies containing response data
- `max_in_flight` controls concurrent requests

### Correlation

- Random ICMP ID per transport instance + sequence number maps to correlation ID
- Responses matched by (id, seq)

### Considerations

- Requires raw sockets (root on Linux)
- Kernel echo replies must be disabled (net.ipv4.icmp_echo_ignore_all=1)
- Some networks filter ICMP
- Payload size varies, typically ~1350 bytes safe on 1500 MTU links

---

## UDP Ephemeral Transport

### Overview

- Alice uses a fresh UDP socket per request and expects one response
- After a response or timeout, the socket is closed and its source port
  enters a cooldown window before reuse
- Bob listens on a single UDP socket and replies once per request
- `max_in_flight` controls concurrent requests

### Considerations

- Default payload MTU is 1350 bytes to avoid fragmentation
- Source port reuse cooldown is configurable (minutes)
- Request/response remains Alice-initiated; Bob only responds to polls
- See `UDP_EPHEMERAL_TRANSPORT.md` for full details

---

## TLS Transports

See `TLS_TRANSPORT.md` for the ClientHello transport and
`doc/completed_plans/TLS_HANDSHAKE_BUMP_TRANSPORT.md` for the TLS bump transport.

### TLS ClientHello

- Encodes packet bytes into TLS ClientHello/ServerHello extensions
- Direct connection or HTTP CONNECT proxy to Bob

### TLS Handshake Bump

- Encodes Alice->Bob requests in SNI under a base domain
- Encodes Bob->Alice responses in CN with checksum framing and fixed-length padding
- Client extracts the response token via scan-only base32 token scanning
- Requires a TLS-bumping proxy that exposes CN in error pages

---

## In-Memory Transport (Testing)

- For local/unit testing with no network dependency
- Backed by in-process queues; MTU defaults to `DEFAULT_MAX_PACKET_SIZE`
- In-flight cap follows `max_in_flight`
- Use `create_inmemory_transport_pair(Config())` to get a connected
  `(Transport, Server)` pair for tests or simulations

---

## Adding New Transports

1. Create `sfb/transport/new_transport.py`
2. Implement `Transport` (client) and/or `Server`
3. Handle medium-specific encoding
4. Implement correlation ID mapping for `send()`/`recv()`
5. Respect `max_in_flight` when tracking in-flight requests

The transport is unaware of tunnel protocol - it just moves encrypted bytes.
