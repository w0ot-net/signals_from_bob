# Tunnel Layer

The tunnel layer orchestrates all lower layers (transport, reliability, crypto,
channels) into a cohesive bidirectional pipe between Alice and Bob.

**Status**: Design specification. Implementation does not exist yet.

---

## Overview

```
Alice (Client)                           Bob (Server)
┌─────────────┐                         ┌─────────────┐
│ AliceTunnel │ ──── request ─────────▶ │  BobTunnel  │
│             │ ◀─── response ───────── │             │
└─────────────┘                         └─────────────┘
```

Alice initiates all communication. Bob cannot reach Alice directly; he can only
respond to Alice's requests. This fundamental asymmetry shapes the entire design.

All transports use a **request/response** pattern via `exchange()` (Alice) and
`recv()`/`responder()` (Bob). See `TRANSPORTS.md` for the transport interface.

---

## Tunnel States

```
DISCONNECTED ──▶ CONNECTING ──▶ CONNECTED ──▶ CLOSING ──▶ CLOSED
                     │                            │
                     └──── (timeout/error) ───────┘
```

- **DISCONNECTED**: Initial state, no connection established.
- **CONNECTING**: Handshake in progress (Alice sent SYN, waiting for SYN+ACK).
- **CONNECTED**: Handshake complete, data can flow.
- **CLOSING**: Graceful shutdown in progress.
- **CLOSED**: Tunnel terminated.

---

## Handshake

The handshake establishes initial sequence numbers and confirms connectivity.

```
Alice                                    Bob
  │                                        │
  │─── SYN (seq=A_ISN, ack=0) ────────────▶│
  │                                        │
  │◀── SYN+ACK (seq=B_ISN, ack=A_ISN+1) ───│
  │                                        │
  │─── ACK (seq=A_ISN+1, ack=B_ISN+1) ────▶│
  │                                        │
  ════════════ CONNECTED ══════════════════
```

- **A_ISN**: Alice's Initial Sequence Number (random)
- **B_ISN**: Bob's Initial Sequence Number (random)
- The SYN flag is set in the packet header's flags field.
- After handshake, both sides know each other's starting sequence.

### Handshake Retransmit

Alice retransmits SYN if no SYN+ACK is received within the initial RTO
(default 1000ms). She backs off exponentially up to MAX_RTO_MS.

Bob has no timer; he simply responds to each SYN he receives with SYN+ACK.
If Alice's final ACK is lost, Bob remains in a "waiting for ACK" state but
will accept data packets as implicit confirmation that Alice received SYN+ACK.

---

## Packet Flow

### Alice's Poll Cycle

Each call to `poll()` performs one request/response exchange using the
transport's `exchange(data) -> bytes` method:

```
1. Check retransmit timers
   └─▶ If RTO expired for any unacked packet, mark for retransmit

2. Collect outgoing data
   ├─▶ Retransmit packets (if any)
   ├─▶ New segments from channel manager
   └─▶ Keepalive if nothing else and interval elapsed

3. Build packet
   ├─▶ seq = next sequence number (or retransmit seq)
   ├─▶ ack = recv_window.ack
   ├─▶ sack = recv_window.sack
   └─▶ segments = collected segments

4. Exchange with Bob
   └─▶ response = transport.exchange(encrypt(packet.encode()))

5. Process response
   ├─▶ Decrypt and decode
   ├─▶ Update send_window with ack/sack (calculate RTT samples)
   ├─▶ Deliver segments to recv_window
   └─▶ Deliver in-order data to channel manager
```

### Bob's Request Handler

Bob uses `transport.recv() -> (data, responder)` to receive Alice's request,
then calls `responder(response_data)` to send the reply:

```
1. Receive request
   └─▶ data, responder = transport.recv(timeout)

2. Decrypt and decode incoming packet

3. Process incoming
   ├─▶ Deliver segments to recv_window
   ├─▶ Deliver in-order data to channel manager
   └─▶ Update ack state

4. Collect outgoing data
   ├─▶ Oldest unacked packet (opportunistic retransmit)
   ├─▶ New segments from channel manager
   └─▶ Respect response MTU limit (transport.send_mtu)

5. Build response packet
   ├─▶ seq = next sequence number
   ├─▶ ack = recv_window.ack
   └─▶ sack = recv_window.sack

6. Send response
   └─▶ responder(encrypt(packet.encode()))
```

---

## Retransmission

### Alice (RTT-based)

Alice maintains an RTT estimator using exponentially weighted moving average
(EWMA) with Karn's algorithm:

```
SRTT = 0.875 * SRTT + 0.125 * sample   (EWMA)
RTO  = 2 * SRTT                         (clamped to [MIN_RTO, MAX_RTO])
```

- On retransmit: RTO doubles (exponential backoff)
- On successful ACK of first-transmission packet: RTO recalculated from sample
- Karn's rule: Retransmitted packets don't contribute RTT samples

Alice checks `send_window.get_retransmits(rto)` each poll cycle and resends
any packets whose time since last send exceeds RTO.

### Bob (Opportunistic)

Bob has no timers. On each request from Alice, Bob includes:
1. The oldest unacked packet (if any) via `send_window.get_oldest_unacked()`
2. New data from channels

Over multiple polls, all unacked packets eventually get retransmitted. This
approach prioritizes the oldest data and avoids flooding responses with
redundant retransmits. If faster recovery is needed, Bob can call
`get_oldest_unacked()` multiple times to include more retransmits.

---

## Keepalive

When no data is pending, Alice sends periodic keepalive packets to:
1. Maintain the connection (detect dead tunnels)
2. Give Bob an opportunity to send data
3. Prevent NAT/firewall timeouts

Keepalive is a packet with:
- Valid seq/ack/sack
- A ping control message on channel 0 (or empty payload)

Bob responds with pong (or his pending data).

Keepalive interval is configurable (default: 5 seconds).

---

## MTU Handling

The tunnel respects transport MTU limits via `transport.send_mtu` and
`transport.recv_mtu` properties:

| Direction | Property | DNS Typical Value |
|-----------|----------|-------------------|
| Alice → Bob | `send_mtu` | ~138 bytes |
| Bob → Alice | `recv_mtu` | ~191 bytes (512) or ~3038 bytes (EDNS 4096) |

The tunnel passes the appropriate MTU to `channel_manager.collect_segments(max_payload)`
to ensure segments fit within transport limits. The packet header (8 bytes) must
also be accounted for.

---

## Encryption

All packets are encrypted before transmission:

```
┌─────────────────────────────────────────┐
│            Encrypted Packet             │
├─────────────────────────────────────────┤
│  encrypt(header + segment1 + segment2)  │
└─────────────────────────────────────────┘
```

Supported ciphers:
- `Plain`: No encryption (testing only)
- `XOR`: Simple XOR with key (lightweight obfuscation)
- `RC4`: RC4 stream cipher

Cipher is configured at tunnel creation. Both sides must use the same cipher
and key.

---

## Error Handling

### Transport Errors

- **Timeout**: No response within transport timeout. Alice retries on next poll.
- **Network Error**: Logged, treated as timeout.
- **Malformed Response**: Logged and ignored.

### Protocol Errors

- **Decryption Failure**: Packet dropped (wrong key or corruption).
- **Invalid Packet**: Packet dropped (malformed header/segments).
- **Unexpected Sequence**: Handled by recv_window (buffered or dropped).

### Connection Loss

Alice detects connection loss when:
- No successful response after N consecutive timeouts (configurable)
- State transitions to CLOSED

Bob detects connection loss when:
- No request received within timeout period (configurable)
- State transitions to CLOSED

---

## Proposed API

The following API is the target design. Implementation does not exist yet.

### AliceTunnel

```python
from sfb.tunnel import AliceTunnel
from sfb.transport.dns import DnsClient
from sfb.crypto import RC4

tunnel = AliceTunnel(
    transport=DnsClient(base_domain='tunnel.example.com'),
    crypto=RC4(key),
    keepalive_interval=5.0,
)

# Connect with handshake
tunnel.connect(timeout=10.0)

# Main loop
while tunnel.connected:
    tunnel.poll()

    # Use channels
    channel = tunnel.channel_manager.open_channel('ipv4', '10.0.0.1', 80)
    channel.write(b'GET / HTTP/1.0\r\n\r\n')
    response = channel.read(4096, timeout=5.0)

tunnel.close()
```

### BobTunnel

```python
from sfb.tunnel import BobTunnel
from sfb.transport.dns import DnsServer
from sfb.crypto import RC4

tunnel = BobTunnel(
    transport=DnsServer(base_domain='tunnel.example.com'),
    crypto=RC4(key),
    idle_timeout=60.0,
)

# Set up channel handler
def on_channel_request(channel_id, atype, addr, port):
    # Connect to target and return True to accept
    return True

tunnel.channel_manager.set_channel_request_handler(on_channel_request)

# Serve requests
tunnel.serve_forever()
# Or handle individually:
# data, responder = tunnel.transport.recv()
# tunnel.handle_request(data, responder)
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `keepalive_interval` | 5.0s | Time between keepalive packets (Alice) |
| `connect_timeout` | 10.0s | Handshake timeout (Alice) |
| `idle_timeout` | 60.0s | Connection timeout with no activity (Bob) |
| `max_retries` | 10 | Max consecutive failures before disconnect |
| `initial_rto` | 1000ms | Initial retransmit timeout (Alice) |

---

## Thread Safety

The tunnel classes are **not thread-safe**. For multi-threaded use:
- Run the tunnel loop in a dedicated thread
- Use thread-safe queues for channel I/O
- Or use the channel's built-in threading primitives (events, locks)

---

## Proposed File Structure

```
sfb/tunnel/
├── __init__.py       # Exports AliceTunnel, BobTunnel
├── base.py           # BaseTunnel with shared functionality
├── alice.py          # AliceTunnel implementation
└── bob.py            # BobTunnel implementation
```
