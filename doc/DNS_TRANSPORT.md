# DNS Transport

This document describes the DNS transport in depth, covering packet encoding,
MTU calculations, and implementation details for both Alice (client) and Bob
(server).

---

## Overview

The DNS transport tunnels data over DNS queries and responses:

- Alice encodes tunnel packets into DNS query names
- Bob runs a DNS server and responds with CNAME records containing tunnel data
- Alice decodes the CNAME target name to recover Bob's packet

This leverages the fact that DNS queries and responses can traverse most
firewalls and networks, making it useful when direct connectivity is blocked.

```
Alice                                           Bob
  │                                               │
  │  A? JBSWY3DP.KNQWG.tunnel.example.com        │
  │──────────────────────────────────────────────▶│
  │                                               │
  │           CNAME <data>.<c>.<base_domain>     │
  │◀──────────────────────────────────────────────│
  │                                               │
```

---

## Operating Modes

The DNS transport supports two operating modes:

### Direct Mode

Alice queries Bob's DNS server directly. No domain delegation required.

```
Alice ────────────────────────────────▶ Bob
      ◀────────────────────────────────
```

**Characteristics:**
- Alice must know Bob's IP address and query it directly
- No domain registration or NS records needed
- Base domain can be anything (e.g., `x.local`)
- Lower latency (no resolver hops)
- Works when Alice can reach Bob on UDP/53

**Configuration:**
- Alice: `resolver` set to Bob's address (required)
- Bob: Listens on configured address

### Authoritative Mode

Bob is configured as the authoritative nameserver for a domain. Alice can
query through any recursive resolver.

```
Alice ──▶ Recursive Resolver ──▶ ... ──▶ Bob
      ◀── Recursive Resolver ◀── ... ◀──
```

**Characteristics:**
- Requires domain registration and NS record configuration
- Works through corporate resolvers, captive portals, etc.
- Higher latency (resolver hops)
- Traffic blends with normal DNS
- Works when Alice cannot reach Bob directly

**Configuration:**
- Alice: `resolver` optional (uses system resolver if unset)
- Bob: Must be authoritative NS for `base_domain`

### Mode Comparison

| Aspect | Direct | Authoritative |
|--------|--------|---------------|
| Setup complexity | Simple | Requires DNS config |
| Domain required | No | Yes |
| Works through resolvers | No | Yes |
| Latency | Lower | Higher |
| Stealth | Lower | Higher |
| Alice reaches Bob directly | Required | Not required |

---

## Timing and Timeouts

Protocol timing (poll deadlines, pending request pruning, and pacing) uses the
shared monotonic clock via `time_provider.now()`. Wall time is reserved for
logging and user-facing timestamps via `time_provider.wall_time()`.

---

## Query Format (Alice → Bob)

Alice encodes tunnel packets into DNS query names using base32 in subdomain
labels, with a nonce prefix for cache busting.

### Structure

```
<nonce>.<label_0>.<label_1>...<label_n>.<base_domain>
```

Where:
- `nonce` is a unique identifier to prevent caching (see Cache Busting below)
- `label_0` through `label_n` contain base32-encoded packet data
- `base_domain` is the configured tunnel domain (e.g., `tunnel.example.com`)

Alice always sends a fresh, uncompressed QNAME. Bob accepts compressed QNAMEs
from recursive resolvers or other intermediaries.

### Example

Tunnel packet (hex): `48 65 6c 6c 6f` ("Hello")

Base32 encoded: `JBSWY3DP`

Query name: `A7B3.JBSWY3DP.tunnel.example.com`

For larger packets, data is split across multiple labels:

```
X9F2.JBSWY3DPEB3W64TMMQQQ.YLNMUQGS3LTEBTG64RAMZXXQ.tunnel.example.com
```

### Cache Busting

Every query includes a unique nonce as the first label. This ensures that:
- Recursive resolvers never return cached responses
- Each query is treated as a fresh lookup
- TTL=0 in responses is reinforced by query uniqueness

The nonce is a 4-character base32 string derived from a counter or random
value. Bob strips the first label before decoding the packet data.

```python
# Alice: generate unique nonce for each query
self.nonce_counter += 1
nonce = base32_encode(self.nonce_counter & 0xFFFF).zfill(4)[:4]
query_name = nonce + '.' + encode_data(packet) + '.' + base_domain

# Bob: strip nonce before decoding
labels = query_name.split('.')
data_labels = labels[1:-len(base_domain.split('.'))]  # skip nonce, skip base_domain
```

The nonce consumes 5 characters of the 253-character name limit (4 chars + dot).
This is a small cost for guaranteed cache bypass.

### DNS Constraints

| Constraint | Limit |
|------------|-------|
| Label length | 63 characters max (50 default for tunnel labels) |
| Total name length | 253 characters max |
| Label count | 127 labels max |

### Base32 Encoding

- Alphabet: A-Z, 2-7 (RFC 4648)
- Case insensitive (DNS is case-insensitive)
- No padding (length derived from decoded data)
- Overhead: 8 bytes → 13 characters (1.625x expansion)

---

## Response Format (Bob → Alice)

Bob encodes tunnel packets into DNS CNAME response targets. To avoid TXT
throttling, the tunnel uses CNAME targets as the response container.

### Standard Format (CNAME)

```
CNAME <data_labels>.<cname_label>.<base_domain>
```

The response target uses the same base32 encoding as queries, without a nonce.
The `cname_label` keeps the suffix short while ensuring the target remains
under the authoritative zone. Use a label that cannot appear in base32 data
(for example, `0`) to avoid collisions with tunnel queries.

### Example

Tunnel packet (hex): `48 65 6c 6c 6f` ("Hello")

Base32 encoded: `JBSWY3DP`

CNAME target: `JBSWY3DP.0.tunnel.example.com`

### DNS Constraints

| Constraint | Limit |
|------------|-------|
| UDP packet size | 512 bytes |
| CNAME target name length | 253 characters max |

Note: With asymmetric MTU negotiation, Alice and Bob advertise independent
send/receive MTUs. Response capacity beyond the query MTU can still help if
the peer accepts a larger receive MTU.

### Base32 Encoding (Responses)

- Alphabet: A-Z, 2-7 (RFC 4648)
- No padding (length derived from decoded data)
- Overhead: 8 bytes → 13 characters (1.625x expansion)

---

## MTU Calculation

The transport computes a per-domain MTU so each tunnel packet fits in a single
DNS query and response. The MTU depends on the base domain length.

### Query-Side MTU

```
available_chars = 253 - len(base_domain) - 1 - 5  # -1 trailing dot, -5 nonce+dot
label_overhead = floor(available_chars / (50 + 1))
usable_chars = available_chars - label_overhead
query_mtu = floor(usable_chars * 5 / 8)           # base32 decode ratio
```

**Example with `tunnel.example.com` (18 chars):**

```
available_chars = 253 - 18 - 1 - 5 = 229
label_overhead = floor(229 / 51) = 4
usable_chars = 229 - 4 = 225
query_mtu = floor(225 * 5 / 8) = 140 bytes
```

### Response-Side MTU

```
available_chars = 253 - len(cname_suffix) - 1
label_overhead = floor(available_chars / (50 + 1))
usable_chars = available_chars - label_overhead
response_mtu = floor(usable_chars * 5 / 8)
```

### Effective MTU

The transport MTU is the minimum of query and response MTU:

```
transport_mtu = min(query_mtu, response_mtu)
```

For `tunnel.example.com`: `min(141, 191) = 141 bytes`

The query side is always the bottleneck due to base32's higher overhead and
DNS name length limits.

### MTU Examples by Domain Length

| Base Domain | Length | Query MTU | Response MTU | Effective MTU |
|-------------|--------|-----------|--------------|---------------|
| `t.co` | 4 | 150 | 191 | 150 |
| `example.com` | 11 | 145 | 191 | 145 |
| `tunnel.example.com` | 18 | 141 | 191 | 141 |
| `sub.tunnel.example.com` | 22 | 138 | 191 | 138 |
| `very.long.subdomain.example.com` | 31 | 133 | 191 | 133 |

Shorter domains provide higher MTU and thus higher throughput.

---

## Label Splitting Algorithm

When encoding a packet into DNS labels, the base32 data must be split to
respect the 63-character label limit, with a nonce prefix for cache busting.

### Encoding Algorithm

```python
def encode_query(packet, base_domain, nonce_counter):
    # Generate cache-busting nonce
    nonce = base32_encode(nonce_counter & 0xFFFF).rstrip('=')[:4]

    # Encode packet data
    b32 = base32_encode(packet).rstrip('=')

    # Split into 63-char labels
    labels = [nonce]
    while b32:
        labels.append(b32[:63])
        b32 = b32[63:]

    return '.'.join(labels) + '.' + base_domain
```

### Example

Packet: 100 bytes
Base32: 160 characters
Nonce: 4 characters
Labels: 1 (nonce) + ceil(160 / 63) = 4 labels

```
A7B3.<63 chars>.<63 chars>.<34 chars>.tunnel.example.com
```

### Decoding Algorithm

```python
def decode_query(query_name, base_domain):
    # Remove base domain suffix
    suffix_len = len(base_domain) + 1  # +1 for dot
    data_part = query_name[:-suffix_len]

    # Split into labels and skip nonce (first label)
    labels = data_part.split('.')
    data_labels = labels[1:]  # skip nonce

    # Concatenate and decode
    b32 = ''.join(data_labels)
    return base32_decode(b32)
```

---

## Alice (Client) Implementation

Alice is the tunnel client. She initiates all DNS queries and processes
responses.

### Configuration

| Parameter | Description | Example |
|-----------|-------------|---------|
| `base_domain` | Tunnel domain suffix | `tunnel.example.com` |
| `resolver` | DNS server to query | `203.0.113.1:53` |
| `query_type` | Query record type | `A` |
| `response_type` | Response record type | `CNAME` |
| `label_max_len` | Max tunnel label length | `50` |
| `cname_label` | Label for CNAME suffix | `0` |
| `timeout` | Query timeout | `5s` |
| `pending_timeout` | Stale query timeout (frees in-flight slots, min 1s) | `10s` |

**Direct mode:** `resolver` must be set to Bob's address. The `base_domain` can
be any valid domain suffix (e.g., `x.local`); it just needs to match Bob's
configuration.

**Authoritative mode:** `resolver` is optional. If unset, Alice reads
`/etc/resolv.conf` to discover system resolvers. The `base_domain` must be the
domain Bob is authoritative for.

### Transport Interface

```python
class DnsTransport(Transport):
    """
    DNS transport with pipelining support.

    Uses non-blocking I/O to manage multiple in-flight queries.
    Correlation IDs map to DNS query IDs internally.
    """

    def __init__(self, base_domain, resolver=None, config=None, timeout=5.0):
        self._socket = socket.socket(AF_INET, SOCK_DGRAM)
        self._socket.setblocking(False)
        self._pending = {}      # corr_id -> _PendingQuery
        self._dns_id_map = {}   # dns_query_id -> corr_id
        self._next_corr_id = 1
        self._max_in_flight = config.max_in_flight

    def send(self, packet: bytes) -> int:
        """Encode packet, send DNS query, return correlation ID."""
        corr_id = self._next_corr_id
        self._next_corr_id += 1

        query_name = self.encode_query(packet)
        dns_id = self.next_dns_id()

        self._pending[corr_id] = _PendingQuery(
            corr_id=corr_id,
            dns_id=dns_id,
            send_time=time_provider.now(),
        )
        self._dns_id_map[dns_id] = corr_id

        self.send_dns_query(query_name, dns_id, qtype=A)
        return corr_id

    def recv(self, timeout: float = None) -> tuple:
        """
        Receive next available response.

        Uses select() to wait for socket readability.
        Returns (corr_id, data) or (None, None) on timeout.
        """
        # Wait for socket readable
        readable, _, _ = select.select([self._socket], [], [], timeout)
        if not readable:
            return (None, None)

        # Read response
        raw, addr = self._socket.recvfrom(4096)
        dns_id, response_data = self.decode_dns_response(raw)

        # Match to correlation ID
        corr_id = self._dns_id_map.pop(dns_id, None)
        if corr_id is None:
            return (None, None)  # Unknown/duplicate

        self._pending.pop(corr_id, None)
        return (corr_id, self.decode_response(response_data))

    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    @property
    def send_mtu(self) -> int:
        return self._send_mtu

    @property
    def recv_mtu(self) -> int:
        return self._recv_mtu

    def close(self):
        self._socket.close()
```

### Usage Patterns

**Serial (max_in_flight=1 or explicit recv after each send):**

```python
config = Config(max_in_flight=1)
transport = DnsTransport(domain, resolver, config=config)
corr_id = transport.send(packet)
corr_id, response = transport.recv(timeout=5.0)
```

**Pipelined:**

```python
config = Config()
transport = DnsTransport(domain, resolver, config=config)

# Alice's tick loop
def tick():
    # Drain available responses (non-blocking)
    while True:
        corr_id, response = transport.recv(timeout=0)
        if corr_id is None:
            break
        process_response(corr_id, response)

    # Send new packets up to limit
    while transport.pending_count() < config.max_in_flight:
        if packet := next_packet():
            transport.send(packet)
        else:
            break
```

Responses may arrive out of order. The reliability layer uses sequence numbers
to reorder them.

### Correlation ID Tracking

The transport maps correlation IDs (returned by `send()`) to DNS query IDs
internally. This allows the tunnel layer to track in-flight packets without
knowing DNS details. Examples below use `time_provider.now()` for monotonic
timestamps:

```python
# Tunnel layer tracks: corr_id -> (seq, send_time, is_retransmit)
    corr_id = transport.send(packet_data)
    in_flight[corr_id] = InFlightPacket(seq, time_provider.now(), is_retransmit=False)

# When response arrives
corr_id, response = transport.recv(timeout=0)
if corr_id in in_flight:
    info = in_flight.pop(corr_id)
    if not info.is_retransmit:
        rtt_sample = time_provider.now() - info.send_time
```

### Timeout Handling

The transport does not retry - that's the reliability layer's job. Stale
pending entries are automatically pruned when no response arrives to free
in-flight capacity. Pruning runs on every `pending_count()`, `send()`, and
`recv()` call, but each send performs a single prune and reuses the result
to avoid redundant O(n) work:

```python
def _prune_stale(self):
    """Remove pending queries older than pending_timeout."""
    now = time_provider.now()
    stale = [cid for cid, pq in self._pending.items()
             if now - pq.send_time > self._pending_timeout]
    for cid in stale:
        dns_id = self._pending[cid].dns_id
        del self._pending[cid]
        self._dns_to_corr.pop(dns_id, None)
```

Pruning cannot be disabled (`pending_timeout` minimum is 1 second). This
prevents transport-level deadlock when all responses are lost - without
pruning, Alice would be permanently stuck waiting for responses that will
never arrive.

---

## Bob (Server) Implementation

Bob is the tunnel server. He runs a DNS server and responds to Alice's queries.

### Configuration

| Parameter | Description | Example |
|-----------|-------------|---------|
| `base_domain` | Tunnel domain suffix to recognize | `tunnel.example.com` |
| `listen_addr` | UDP address to listen on | `0.0.0.0:53` |
| `query_type` | Query record type | `A` |
| `response_type` | Response record type | `CNAME` |
| `label_max_len` | Max tunnel label length | `50` |
| `cname_label` | Label for CNAME suffix | `0` |
| `cname_a_addr` | A record for CNAME follow-ups | `0.0.0.0` |
| `ttl` | TTL for responses | `0` (no caching) |

### DNS Server Setup

**Direct mode:** No special DNS configuration required. Bob listens on a UDP
port, and Alice queries him directly. The `base_domain` just needs to match
Alice's configuration; it doesn't need to be a real domain.

```
# Bob listens on 203.0.113.1:53
# Alice queries 203.0.113.1:53 directly
# base_domain can be anything, e.g., "x.local"
```

**Authoritative mode:** Bob must be configured as the authoritative nameserver
for `base_domain`. This requires:

1. Domain registration or control of parent zone
2. NS record pointing to Bob's server
3. Glue record (A record for the nameserver if self-hosted)
4. Firewall: UDP port 53 open

Example DNS zone for `example.com`:

```
tunnel    IN    NS    ns1.example.com.
ns1       IN    A     203.0.113.1          ; Bob's IP
```

With this configuration, queries for `*.tunnel.example.com` are routed to Bob.

### Transport Interface

```python
class DnsTransport:
    def recv(self) -> (bytes, callable):
        """Receive DNS query and decode packet. Blocking."""
        while True:
            query, client_addr = self.recv_dns_query()  # blocks
            if not query.name.endswith(self.base_domain):
                continue  # Ignore non-tunnel queries
            decoded = self.decode_query(query.name)
            def responder(packet):
                response_data = self.encode_response(packet)
                self.send_dns_response(query.id, client_addr, response_data)
            return decoded, responder

    def close(self) -> None:
        """Close UDP socket."""
        self.socket.close()

    @property
    def recv_mtu(self) -> int:
        """Maximum bytes that can be received in one poll."""
        return self._recv_mtu

    @property
    def send_mtu(self) -> int:
        """Maximum bytes that can be sent in one response."""
        return self._send_mtu
```

### Serial Processing

Bob must respond to each query before receiving the next:

```python
while running:
    alice_packet, responder = transport.recv()   # blocks
    bob_packet = process(alice_packet)           # tunnel processing
    responder(bob_packet)                        # respond to that query
```

This is not a bottleneck—tunnel processing is sub-millisecond. Throughput is
limited by Alice's query rate, not Bob's processing speed.

### Query Context Tracking

Between `recv()` and the responder callback, Bob holds the query context
(query ID and client address) to construct the matching response.

### Response TTL

Bob should set TTL=0 on tunnel responses to prevent caching:

```python
def send_dns_response(self, query_id, client_addr, response_data):
    response = DnsResponse()
    response.id = query_id
    response.add_answer(
        name=self.base_domain,
        qtype=CNAME,
        ttl=0,              # No caching
        data=response_data
    )
    self.socket.sendto(response.encode(), client_addr)
```

Caching would cause stale data and break the tunnel.

### Handling Non-Tunnel Queries

In authoritative mode, Bob may receive legitimate DNS queries for the domain
(e.g., SOA, NS from resolvers). He should respond appropriately:

```python
# cname_suffix = cname_label + '.' + base_domain
def recv_dns_query(self):
    while True:
        data, addr = self.socket.recvfrom(512)
        query = DnsQuery.decode(data)

        if query.qtype == A and query.name.endswith(self.base_domain):
            return query, addr

        if query.qtype == A and query.name.endswith(cname_suffix):
            self.send_a_response(query, addr)
            continue

        # Handle other query types minimally (authoritative mode)
        if query.qtype == SOA:
            self.send_soa_response(query, addr)
        elif query.qtype == NS:
            self.send_ns_response(query, addr)
        else:
            self.send_nxdomain(query, addr)
```

In direct mode, Bob can simply ignore non-A queries since Alice only sends A
queries for tunnel data.

### Outbound Buffer

Bob queues outbound packets. When Alice polls:

1. Decode incoming packet from query
2. Pass to reliability/muxer layers
3. Check outbound queue for response data
4. If data: encode and send
5. If no channel data is queued: send keepalive-only packet (FLAG_KEEPALIVE,
   zero segments)

The response always contains a valid tunnel packet (with seq/ack headers). When
no data is queued, the packet carries no segments and sets FLAG_KEEPALIVE.

---

## DNS Protocol Details

### Query Structure

```
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|                      ID                       |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|QR|   Opcode  |AA|TC|RD|RA|   Z    |   RCODE   |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|                    QDCOUNT                    |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|                    ANCOUNT                    |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|                    NSCOUNT                    |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|                    ARCOUNT                    |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
```

For tunnel queries:
- ID: Random, used to match responses
- QR: 0 (query)
- RD: 1 (recursion desired, for traversing resolvers)
- QDCOUNT: 1
- QTYPE: A (1)
- QCLASS: IN (1)

### Response Structure

For tunnel responses:
- ID: Copied from query
- QR: 1 (response)
- AA: 1 (authoritative)
- ANCOUNT: 1
- Answer: CNAME record carrying tunnel data in the target name

### Resolver Traversal (Authoritative Mode)

When Alice uses a recursive resolver (authoritative mode without direct query):

```
Alice → Recursive Resolver → ... → Bob
      ← Recursive Resolver ← ... ←
```

The tunnel works through resolvers because:
- A queries are passed through unchanged
- CNAME responses are returned to Alice (often with an A follow-up)
- TTL=0 prevents caching

However, some resolvers may:
- Rate-limit queries to the same domain
- Cache despite TTL=0 (misbehaving)
- Modify or truncate CNAME responses

For lower latency and reliability, use direct mode or authoritative mode with
direct query when Alice can reach Bob on UDP/53.

---

## Error Handling

### Alice Errors

| Error | Handling |
|-------|----------|
| No response (timeout) | Reliability layer retransmits |
| Malformed response | Drop, reliability retransmits |
| Truncated response | Drop, reliability retransmits |
| NXDOMAIN / SERVFAIL | Log, reliability retransmits |

### Bob Errors

| Error | Handling |
|-------|----------|
| Malformed query | Ignore (no response) |
| Wrong domain suffix | Ignore |
| Base32 decode failure | Ignore |
| Packet too large for response | Should not happen (MTU enforced) |

### Network Errors

| Error | Handling |
|-------|----------|
| UDP socket error | Retry or reconnect socket |
| ICMP port unreachable | Log, continue |

---

## Configuration Examples

### Direct Mode (High Performance)

No domain setup required. Alice queries Bob directly with maximum performance.

**Alice:**
```python
transport = DnsTransport(
    base_domain='x',                 # Shortest possible domain
    resolver='203.0.113.1:53',       # Bob's address (required)
    timeout=5.0
)
```

**Bob:**
```python
transport = DnsTransport(
    base_domain='x',                 # Must match Alice
    listen_addr='0.0.0.0:53',
    ttl=0
)
```

This configuration provides maximum throughput: ~150 bytes per packet
with 16x pipelining.

### Authoritative Mode

Requires DNS delegation to Bob.

**Alice:**
```python
transport = DnsTransport(
    base_domain='tunnel.example.com',
    resolver=None,                   # Use system resolver
    timeout=5.0
)
```

**Bob:**
```python
transport = DnsTransport(
    base_domain='tunnel.example.com',
    listen_addr='0.0.0.0:53',
    ttl=0
)
```

**DNS Setup:**
1. Register domain or use existing: `example.com`
2. Create subdomain for tunnel: `tunnel.example.com`
3. Set NS record: `tunnel.example.com NS ns.example.com`
4. Set A record for NS: `ns.example.com A <bob_ip>`
5. Run Bob on `<bob_ip>:53`

### Authoritative Mode with Direct Query

Alice queries Bob directly but uses a real domain (for fallback capability).

**Alice:**
```python
transport = DnsTransport(
    base_domain='tunnel.example.com',
    resolver='203.0.113.1:53',       # Query Bob directly for lower latency
    timeout=5.0
)
```

This combines the reliability of a real domain (can fall back to resolver)
with the lower latency of direct queries.

---

## Performance Optimization

This section describes techniques to maximize throughput over the DNS transport.

### Baseline Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Query overhead | ~30 bytes | DNS header + question overhead |
| Response overhead | ~45 bytes | DNS header + answer overhead |
| Encoding overhead (query) | 1.625x | Base32 |
| Encoding overhead (response) | 1.625x | Base32 in CNAME target |
| Base MTU | 130-150 bytes (query), ~125-145 bytes (response) | Depends on suffix length |

### Optimal Domain Selection

Shorter base domains provide higher query-side MTU:

| Base Domain | Query MTU | Improvement |
|-------------|-----------|-------------|
| `t.co` | 150 | +10 bytes vs 19-char domain |
| `a.io` | 150 | +10 bytes |
| `x.local` (direct mode) | 148 | +8 bytes |
| `tunnel.example.com` | 140 | baseline |

For direct mode, use the shortest possible domain (e.g., `x.x` = 3 chars).

### Aggressive Pipelining

Alice should maintain `max_in_flight` queries in-flight at all times
(assume `config` is the active `Config` instance):

```python
# Optimal pipelining loop
while data_to_send or transport.pending_count() > 0:
    # Send up to max_in_flight
    while (transport.pending_count() < config.max_in_flight and
           data_to_send):
        packet = next_packet()
        corr_id = transport.send(packet)
        in_flight[corr_id] = packet.seq

    # Drain all available responses (non-blocking)
    while True:
        corr_id, response = transport.recv(timeout=0)
        if corr_id is None:
            break
        in_flight.pop(corr_id, None)
        process(response)
```

With `max_in_flight=16` and 100ms RTT:
- Sequential: 10 packets/second
- Pipelined: 160 packets/second (16x improvement)

### Query Rate Optimization

For direct mode, Alice can send queries as fast as the network allows:

```python
# High-performance query loop (direct mode)
while running:
    # Send burst of queries up to max_in_flight
    while transport.pending_count() < config.max_in_flight:
        if packet := next_outbound():
            transport.send(packet)
        else:
            break

    # Process responses with short timeout
    while True:
        corr_id, response = transport.recv(timeout=0.01)
        if corr_id is None:
            break
        process(response)
```

### Response Packing

Bob should maximize data per response by filling to the MTU:

```python
def prepare_response(mtu):
    segments = []
    size = 8  # tunnel header

    while size < mtu and outbound_queue:
        segment = outbound_queue.peek()
        segment_size = 3 + len(segment.data)  # segment header + data

        if size + segment_size <= mtu:
            segments.append(outbound_queue.pop())
            size += segment_size
        else:
            break

    return build_packet(segments)
```

### Performance Summary

| Configuration | Query MTU | Response MTU | Effective |
|---------------|-----------|--------------|-----------|
| Standard CNAME | 140 | 140 | 140 |
| Short domain (direct) | 150 | 150 | 150 |

The bottleneck is the smaller of the query-side and response-side MTUs.

### Throughput Estimates

The estimates below assume CNAME responses and a short CNAME suffix.

| Scenario | Packets/s | Payload/pkt | Throughput |
|----------|-----------|-------------|------------|
| Sequential, 100ms RTT | 10 | 130 | 1.3 KB/s |
| Pipelined x8, 100ms RTT | 80 | 130 | 10.4 KB/s |
| Pipelined x16, 100ms RTT | 160 | 130 | 20.8 KB/s |
| Pipelined x16, 50ms RTT | 320 | 130 | 41.6 KB/s |
| Direct mode, 10ms RTT, x16 | 1600 | 140 | 224 KB/s |

Maximum theoretical throughput in ideal conditions (direct mode, minimal RTT,
max pipelining): **200+ KB/s**

Practical throughput with authoritative mode through resolvers: **10-50 KB/s**
