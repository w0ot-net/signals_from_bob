# DNS Transport

This document describes the DNS transport in depth, covering packet encoding,
MTU calculations, and implementation details for both Alice (client) and Bob
(server).
The transport is IPv4-only; IPv6 is unsupported.

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

The transport is asymmetric: Alice initiates all DNS queries (polls) and Bob
only responds to those queries. Bob cannot send unsolicited traffic, and his
throughput is bounded by Alice's polling rate.

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
- Alice must know Bob's IP:port address and query it directly
- No domain registration or NS records needed
- Base domain can be anything (e.g., `x.local`)
- Lower latency (no resolver hops)
- Works when Alice can reach Bob on the configured UDP port

**Configuration:**
- Alice: `dns_resolver` set to Bob's address (required)
- Bob: Listens on configured address (direct mode commonly uses port 5353 for
  local tests; port 53 also works with sufficient privileges)

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
- Requires Bob to listen on UDP/53 for resolver reachability

**Configuration:**
- Alice: `dns_resolver` optional (uses system resolver if unset; `/etc/resolv.conf`
  on Unix and `nslookup` output on Windows)
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

The nonce is a 4-character base32 string derived from a 16-bit counter that
starts at a random value and increments for each query. Bob skips the nonce
label before decoding the packet data.

```python
# Alice: generate unique nonce for each query
self._nonce = (self._nonce + 1) & 0xFFFF
nonce_label = base32_encode(struct.pack('>H', self._nonce))[:4]
query_name = nonce_label + '.' + encode_data(packet, label_max_len) + '.' + base_domain

# Bob: decode query (skips nonce and base domain)
data = decode_query_name(query_name, base_domain, label_max_len)
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

Resolvers may issue follow-up A queries for the CNAME target. Bob answers those
queries using `dns_cname_a_addr` (default `0.0.0.0`).

### DNS Constraints

| Constraint | Limit |
|------------|-------|
| UDP packet size | 512 bytes (EDNS0 payload size is capped at 512 by config) |
| CNAME target name length | 253 characters max |

`dns_recv_bufsize_min` only affects the socket recv buffer; it does not change
the on-wire DNS size limit.

Note: With asymmetric MTU negotiation, Alice and Bob advertise independent
send/receive MTUs. Response capacity beyond the query MTU can still help if
the peer accepts a larger receive MTU.

### Base32 Encoding (Responses)

- Alphabet: A-Z, 2-7 (RFC 4648)
- No padding (length derived from decoded data)
- Overhead: 8 bytes → 13 characters (1.625x expansion)

---

## MTU Calculation

The DNS transport exposes per-direction packet MTUs:

- Alice send_packet_mtu / Bob recv_packet_mtu: query-side MTU (encoded in the QNAME).
- Alice recv_packet_mtu / Bob send_packet_mtu: response-side MTU (encoded in the CNAME target).

These values depend on `base_domain`, `cname_label`, and `label_max_len`. The
tunnel negotiates tx/rx MTUs independently (asymmetric MTU).
Payload bytes are derived as `(packet_mtu - PACKET_HEADER_SIZE)` when packing
segments.

### Query-Side MTU (Alice send_packet_mtu, Bob recv_packet_mtu)

```
available_chars = 253 - len(base_domain) - 1 - 4 - 1  # nonce is 4 chars
label_overhead = floor(available_chars / (label_max_len + 1))
usable_chars = available_chars - label_overhead
query_mtu = floor(usable_chars * 5 / 8)           # base32 decode ratio
```

**Example with `tunnel.example.com` (18 chars, label_max_len=50):**

```
available_chars = 253 - 18 - 1 - 4 - 1 = 229
label_overhead = floor(229 / 51) = 4
usable_chars = 229 - 4 = 225
query_mtu = floor(225 * 5 / 8) = 140 bytes
```

### Response-Side MTU (Alice recv_packet_mtu, Bob send_packet_mtu)

```
cname_suffix = cname_label + '.' + base_domain
available_chars = 253 - len(cname_suffix) - 1
label_overhead = floor(available_chars / (label_max_len + 1))
usable_chars = available_chars - label_overhead
response_mtu = floor(usable_chars * 5 / 8)
```

**Example with `tunnel.example.com`, cname_label=`0`, label_max_len=50:**

```
response_mtu = 142 bytes
```

### CNAME Response Caps and Adaptive Clamp

When `dns_response_type=CNAME`, the full DNS response must fit within the EDNS
size (minimum 512 bytes) and includes the original QNAME in both the question
and answer sections. This means the usable response packet size depends on the
query's QNAME wire length.

The client precomputes a lookup across all possible query packet sizes using
the same sizing rules as the server (EDNS clamp plus OPT record length) to
derive a per-query `response_payload_cap` in packet bytes. The maximum of that
lookup bounds Alice's `recv_packet_mtu` and Bob's `send_packet_mtu`; init fails
if the maximum response packet size is smaller than the minimum packet needed
to carry one segment.

Alice selects a per-send query cap based on adaptive clamp mode:
- response_max while retransmits may be pending (maximize Bob's response size)
- balanced when both sides have data
- idle when Bob has no data (keep response slots small)
The chosen packet cap is attached to the send permit and applied at packet
build time, so segments are sized before encoding the DNS query. Bob still
enforces the per-request response cap for each response and retransmit.
The DNS server attaches `response_payload_cap` to the responder so Bob can
apply the per-request cap for both new sends and retransmits.

Response caps and MTUs are packet bytes; payload bytes are
(`packet_mtu` - `PACKET_HEADER_SIZE`).

### MTU Examples by Domain Length

| Base Domain | Length | Query MTU | Response MTU | CNAME Response Cap (512) |
|-------------|--------|-----------|--------------|-------------------------|
| `t.co` | 4 | 149 | 151 | 93 |
| `example.com` | 11 | 145 | 146 | 88 |
| `tunnel.example.com` | 18 | 140 | 142 | 84 |
| `sub.tunnel.example.com` | 22 | 138 | 140 | 81 |
| `very.long.subdomain.example.com` | 31 | 132 | 134 | 76 |

Shorter domains and shorter `cname_label` values provide higher MTU and payload
cap, which improves throughput.

Values above assume `dns_label_max_len=50`, `dns_cname_label=0`, and
`dns_edns_size=512`.

---

## Label Splitting Algorithm

When encoding a packet into DNS labels, the base32 data is split to respect
`label_max_len` (default 50, max 63), with a nonce prefix for cache busting.

### Encoding Algorithm

```python
def encode_query(packet, base_domain, nonce_counter, label_max_len):
    # Generate cache-busting nonce
    nonce = base32_encode(struct.pack('>H', nonce_counter & 0xFFFF))[:4]

    # Encode packet data
    b32 = base32_encode(packet).rstrip('=')

    # Split into label_max_len labels
    labels = [nonce]
    while b32:
        labels.append(b32[:label_max_len])
        b32 = b32[label_max_len:]

    return '.'.join(labels) + '.' + base_domain
```

### Example

Packet: 100 bytes
Base32: 160 characters
Nonce: 4 characters
Labels: 1 (nonce) + ceil(160 / 50) = 5 labels

```
A7B3.<50 chars>.<50 chars>.<50 chars>.<10 chars>.tunnel.example.com
```

### Decoding Algorithm

```python
def decode_query(query_name, base_domain, label_max_len):
    # decode_query_name validates suffix and skips the nonce label
    return decode_query_name(query_name, base_domain, label_max_len)
```

---

## Alice (Client) Implementation

Alice is the tunnel client. She initiates all DNS queries and processes
responses.

### Configuration

| Parameter | Description | Example |
|-----------|-------------|---------|
| `dns_base_domain` | Tunnel domain suffix | `tunnel.example.com` |
| `dns_resolver` | DNS server to query (host:port) | `203.0.113.1:53` |
| `dns_query_type` | Query record type (fixed to `A`) | `A` |
| `dns_response_type` | Response record type (fixed to `CNAME`) | `CNAME` |
| `dns_label_max_len` | Max tunnel label length (4-63) | `50` |
| `dns_cname_label` | Label for CNAME suffix | `0` |
| `dns_edns_size` | UDP payload size (must be <= 512) | `512` |
| `dns_recv_bufsize_min` | Minimum UDP recv buffer size | `4096` |
| `dns_pending_timeout` | Stale query timeout (frees in-flight slots, min 1s) | `5s` |

**Direct mode:** `dns_resolver` must be set to Bob's address. The `base_domain` can
be any valid domain suffix (e.g., `x.local`); it just needs to match Bob's
configuration.

**Authoritative mode:** `dns_resolver` is optional. If unset, Alice loads system
resolvers from `/etc/resolv.conf` on Unix or `nslookup` output on Windows. The
`base_domain` must be the domain Bob is authoritative for.

### Transport Interface

```python
from sfb.transport.dns import DnsClient

client = DnsClient(config)

permit = client.reserve_send()
if permit is None:
    raise RuntimeError('capacity exhausted')
corr_id = client.send(packet, permit)
corr_id, response = client.recv(timeout=5.0)
```

`send()` requires a `SendPermit` from `reserve_send()`. Use `release_send()` if
you reserve a permit but skip a send. `recv(timeout)` returns `(corr_id, data)`
or `(None, None)` on timeout.

### Usage Patterns

**Serial (max_in_flight=1 or explicit recv after each send):**

```python
config = Config(
    dns_base_domain=domain,
    dns_resolver=resolver,
    max_in_flight=1,
)
client = DnsClient(config)
permit = client.reserve_send()
if permit is None:
    raise RuntimeError('capacity exhausted')
corr_id = client.send(packet, permit)
corr_id, response = client.recv(timeout=5.0)
```

**Pipelined:**

```python
config = Config(
    dns_base_domain=domain,
    dns_resolver=resolver,
)
client = DnsClient(config)

# Alice's tick loop
def tick():
    # Drain available responses (non-blocking)
    while True:
        corr_id, response = client.recv(timeout=0)
        if corr_id is None:
            break
        process_response(corr_id, response)

    # Send new packets up to limit
    while True:
        packet = next_packet()
        if packet is None:
            break
        permit = client.reserve_send()
        if permit is None:
            break
        client.send(packet, permit)
```

Responses may arrive out of order. The reliability layer uses sequence numbers
to reorder them.

### Correlation ID Tracking

The transport maps correlation IDs (returned by `send()` after reserving a
permit) to DNS query IDs
internally. This allows the tunnel layer to track in-flight packets without
knowing DNS details. Examples below use `time_provider.now()` for monotonic
timestamps:

```python
# Tunnel layer tracks: corr_id -> (seq, send_time, is_retransmit)
    permit = client.reserve_send()
    if permit is None:
        return
    corr_id = client.send(packet_data, permit)
    in_flight[corr_id] = InFlightPacket(seq, time_provider.now(), is_retransmit=False)

# When response arrives
corr_id, response = client.recv(timeout=0)
if corr_id in in_flight:
    info = in_flight.pop(corr_id)
    if not info.is_retransmit:
        rtt_sample = time_provider.now() - info.send_time
```

### Timeout Handling

The transport does not retry - that's the reliability layer's job. Stale
pending entries are automatically pruned when no response arrives to free
in-flight capacity. Pruning runs in `reserve_send()` and `recv()`; the
`pending_count()` accessor is non-pruning. Each send attempt performs a
single prune and reuses the result to avoid redundant O(n) work:

```python
def _prune_stale(self):
    """Remove pending queries older than pending_timeout."""
    stale = self._pending.prune(now=time_provider.now())
    for _, pending in stale:
        self._dns_to_corr.pop(pending.dns_id, None)
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
| `dns_base_domain` | Tunnel domain suffix to recognize | `tunnel.example.com` |
| `dns_listen_addr` | UDP address to listen on | `0.0.0.0:53` |
| `dns_query_type` | Query record type (fixed to `A`) | `A` |
| `dns_response_type` | Response record type (fixed to `CNAME`) | `CNAME` |
| `dns_label_max_len` | Max tunnel label length (4-63) | `50` |
| `dns_cname_label` | Label for CNAME suffix | `0` |
| `dns_cname_a_addr` | A record for CNAME follow-ups | `0.0.0.0` |
| `dns_edns_size` | UDP payload size (must be <= 512) | `512` |
| `dns_recv_bufsize_min` | Minimum UDP recv buffer size | `4096` |

### DNS Server Setup

**Direct mode:** No special DNS configuration required. Bob listens on a UDP
port, and Alice queries him directly. The `base_domain` just needs to match
Alice's configuration; it doesn't need to be a real domain.

```
# Bob listens on 203.0.113.1:5353
# Alice queries 203.0.113.1:5353 directly
# base_domain can be anything, e.g., "x.local"
```

**Authoritative mode:** Bob must be configured as the authoritative nameserver
for `base_domain`. This requires:

1. Domain registration or control of parent zone
2. NS record pointing to Bob's server
3. Glue record (A record for the nameserver if self-hosted)
4. Firewall: UDP port 53 open (authoritative mode expects port 53)

Example DNS zone for `example.com`:

```
tunnel    IN    NS    ns1.example.com.
ns1       IN    A     203.0.113.1          ; Bob's IP
```

With this configuration, queries for `*.tunnel.example.com` are routed to Bob.

### Transport Interface

```python
from sfb.transport.dns import DnsServer

server = DnsServer(config)
data, responder = server.recv(timeout=None)
if data is not None:
    response_data = process(data)
    responder(response_data)
```

`recv()` returns `(None, None)` on timeout. The `responder` callable sends the
response for the corresponding query.

### Serial Processing

Bob must respond to each query before receiving the next:

```python
while running:
    alice_packet, responder = server.recv()      # blocks
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
def send_dns_response(self, query_id, qname, client_addr, response_data):
    response = DnsResponse()
    response.id = query_id
    response.add_answer(
        name=qname,
        qtype=CNAME,
        ttl=0,              # No caching
        data=response_data
    )
    self.socket.sendto(response.encode(), client_addr)
```

Caching would cause stale data and break the tunnel.

### Handling Non-Tunnel Queries

In authoritative mode, Bob may receive resolver follow-ups and other queries.
The DNS server handles them minimally to avoid timeouts:

```python
def recv_dns_query(self):
    while True:
        data, addr = self.socket.recvfrom(512)
        query = DnsQuery.decode(data)

        if not query.name.endswith(self.base_domain):
            continue

        if query.qtype != A:
            self.send_empty_response(query, addr, reason='qtype_mismatch')
            continue

        if query.name.endswith(cname_suffix):
            self.send_cname_followup(query, addr)
            continue

        try:
            return decode_query_name(query.name, self.base_domain), addr
        except ValueError:
            self.send_empty_response(query, addr, reason='decode_failed')
            continue
```

In direct mode, Bob still ignores queries outside `base_domain` and returns
empty responses for qtype mismatches or decode failures to avoid resolver
timeouts.

### Outbound Buffer

Bob queues outbound packets. When Alice polls:

1. Decode incoming packet from query
2. Pass to reliability/muxer layers
3. Check outbound queue for response data
4. If data: encode and send
5. If no channel data is queued: send `KEEPALIVE` (FLAG_KEEPALIVE, zero
   segments)
6. If a retransmit cannot fit within the per-request response cap: send
   `KEEPALIVE` + `POLL_HINT` (zero segments) and keep the retransmit queued

The response always contains a valid tunnel packet (with seq/ack headers). When
no data is queued, the packet carries no segments and sets FLAG_KEEPALIVE. When
pending data exists, Bob includes segments if they fit; otherwise he uses a
KEEPALIVE + POLL_HINT response to keep Alice polling.

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
- CNAME responses are returned to Alice (often with an A follow-up, which Bob
  answers using `dns_cname_a_addr`)
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
| QTYPE mismatch | Send empty NOERROR+SOA (avoid resolver timeouts) |
| Base32 decode failure | Send empty NOERROR+SOA (avoid resolver timeouts) |
| Response encode error | Log and drop response |

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
config = Config(
    dns_base_domain='x',              # Shortest possible domain
    dns_resolver='203.0.113.1:5353',  # Bob's address (required)
)
client = DnsClient(config)
```

**Bob:**
```python
config = Config(
    dns_base_domain='x',              # Must match Alice
    dns_listen_addr='0.0.0.0:5353',
)
server = DnsServer(config)
```

This configuration uses an unprivileged port for local tests. Use port 53 if
you can bind it directly.

### Authoritative Mode

Requires DNS delegation to Bob.

**Alice:**
```python
config = Config(
    dns_base_domain='tunnel.example.com',
    dns_resolver=None,               # Use system resolver
)
client = DnsClient(config)
```

**Bob:**
```python
config = Config(
    dns_base_domain='tunnel.example.com',
    dns_listen_addr='0.0.0.0:53',
)
server = DnsServer(config)
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
config = Config(
    dns_base_domain='tunnel.example.com',
    dns_resolver='203.0.113.1:53',   # Query Bob directly for lower latency
)
client = DnsClient(config)
```

This combines the reliability of a real domain (can fall back to resolver)
with the lower latency of direct queries.

---

## Performance Optimization

This section describes techniques to maximize throughput over the DNS transport.

### Baseline Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Query overhead | 12 + qname_len + 4 bytes | DNS header + question |
| Response overhead | 12 + qname_len + 4 + qname_len + 10 bytes | Header + question + answer (without RDATA) |
| Encoding overhead (query/response) | 1.625x | Base32 in QNAME/CNAME |
| MTU (label_max_len=50) | 132-151 bytes (query), 134-153 bytes (response) | Depends on domain and cname_label |
| CNAME response cap (512) | 76-94 bytes | Depends on domain and cname_label |

### Optimal Domain Selection

Shorter base domains and shorter `dns_cname_label` values provide higher MTU
and response caps:

| Base Domain | Query MTU | CNAME Response Cap (512) |
|-------------|-----------|-------------------------|
| `t.co` | 149 | 93 |
| `x.local` (direct mode) | 147 | 91 |
| `tunnel.example.com` | 140 | 84 |

For direct mode, use the shortest possible domain (e.g., `x.x` or `x`) and a
short `dns_cname_label`.

### Aggressive Pipelining

Alice should maintain `max_in_flight` queries in-flight at all times
(assume `config` is the active `Config` instance):

```python
# Optimal pipelining loop
while data_to_send or client.pending_count() > 0:
    # Send up to max_in_flight
    while data_to_send:
        packet = next_packet()
        permit = client.reserve_send()
        if permit is None:
            break
        corr_id = client.send(packet, permit)
        in_flight[corr_id] = packet.seq

    # Drain all available responses (non-blocking)
    while True:
        corr_id, response = client.recv(timeout=0)
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
    while True:
        packet = next_outbound()
        if packet is None:
            break
        permit = client.reserve_send()
        if permit is None:
            break
        client.send(packet, permit)

    # Process responses with short timeout
    while True:
        corr_id, response = client.recv(timeout=0.01)
        if corr_id is None:
            break
        process(response)
```

### Response Packing

Bob should maximize data per response by filling to the MTU:

```python
def prepare_response(mtu):
    segments = []
    size = 38  # tunnel header

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

| Configuration | Query MTU | Response MTU | CNAME Response Cap (512) |
|---------------|-----------|--------------|-------------------------|
| `tunnel.example.com` | 140 | 142 | 84 |
| `x` (direct mode) | 151 | 153 | 94 |

With 512-byte DNS responses, the CNAME response cap often becomes the limiting
factor for response payload size.

### Throughput Estimates

The estimates below assume CNAME responses with `tunnel.example.com` (response
cap 84 bytes) unless noted otherwise. Payload bytes refer to tunnel packet
bytes; application payload is smaller due to protocol headers.

| Scenario | Packets/s | Payload/pkt | Throughput |
|----------|-----------|-------------|------------|
| Sequential, 100ms RTT | 10 | 84 | 0.84 KB/s |
| Pipelined x8, 100ms RTT | 80 | 84 | 6.7 KB/s |
| Pipelined x16, 100ms RTT | 160 | 84 | 13.4 KB/s |
| Pipelined x16, 50ms RTT | 320 | 84 | 26.9 KB/s |
| Direct mode, 10ms RTT, x16 (`x`) | 1600 | 94 | 150 KB/s |

Maximum theoretical throughput in ideal conditions (direct mode, minimal RTT,
max pipelining): **~150 KB/s**

Practical throughput with authoritative mode through resolvers is usually
lower due to resolver latency and rate limiting.
