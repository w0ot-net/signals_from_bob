# Tunnel Layer

The tunnel layer orchestrates all lower layers (transport, reliability, crypto,
channels) into a cohesive bidirectional pipe between Alice and Bob.

**Implementation**: `sfb/tunnel/`

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

All transports use a **request/response** pattern via `send()`/`recv()` (Alice)
and `recv()`/`responder()` (Bob). See `TRANSPORTS.md` for the transport interface.

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
  │─── SYN (seq=1, ack=0) ────────────────▶│
  │                                        │
  │◀── SYN+ACK (seq=1, ack=2) ─────────────│
  │                                        │
  │─── ACK (seq=2, ack=2) ────────────────▶│
  │                                        │
  ════════════ CONNECTED ══════════════════
```

- Initial sequence number (ISN) is fixed at 1 for both sides.
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

### Alice's Tick Cycle

Each call to `tick()` drains available responses, handles retransmits,
and sends new packets using `send()`/`recv()`:

```
1. Drain available responses
   ├─▶ corr_id, data = transport.recv(timeout=0)
   └─▶ Decrypt, decode, update send_window (ACK/SACK + cumulative ACK tracking),
       deliver segments

2. Check retransmit timers
   ├─▶ If RTO expired for any unacked packet, retransmit (rebuild with fresh ack/sack)
   └─▶ If SACK progress shows a missing cumulative ACK hole, fast retransmit it

3. Send new packets (while capacity remains)
   ├─▶ Collect outgoing segments
   ├─▶ Build packet with seq/ack/sack
   ├─▶ permit = transport.reserve_send()
   └─▶ transport.send(header + encrypt(segments), permit)
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
   └─▶ Respect response MTU limit (negotiated MTU)

5. Build response packet
   ├─▶ seq = next sequence number
   ├─▶ ack = recv_window.ack
   └─▶ sack = recv_window.sack

6. Send response
   └─▶ responder(header + encrypt(segments))
```


### Correlation IDs

Alice's transport `send()` returns a correlation ID that the transport uses to
match responses to requests. The tunnel itself does not interpret or rely on
the correlation ID; it is only used by the transport to demultiplex responses.
Alice drains responses with `recv()` until `(None, None)` is returned.

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
any packets whose time since last send exceeds RTO. Retransmits reuse the
original sequence number and are rebuilt with current ack/sack.
If SACK progress is observed while the cumulative ACK is stalled, Alice can
fast retransmit the missing sequence before the RTO expires.

Retransmits are gated on cumulative ACK silence (time since ACK advanced),
not on response silence. Responses without ACK progress do not defer RTO
retransmits; response silence is only used for connection timeout.

### Bob (Opportunistic)

Bob does not run a background retransmit timer. On each request from Alice, Bob
may include the oldest unacked packet (if any) when it passes the retransmit
cooldown and cumulative ACKs are not advancing, otherwise the retransmit is
skipped for that poll. After that, Bob adds new data from channels.

Over multiple polls, the oldest unacked packet is retried once its cooldown
expires and ACK progress stalls. This approach prioritizes the oldest data and
avoids flooding responses with redundant retransmits. The current implementation
sends at most one retransmit per response; additional retransmits wait for later
polls.

The total number of unacked packets remains capped at max_in_flight, so the
SACK bitmap always covers all outstanding packets.

With adaptive pacing enabled, Alice gates new sends using the pacer target
inflight. The send-window distance guard still uses the negotiated
max_in_flight (SACK window) to avoid overrunning the peer's SACK coverage.
Pacing does not tighten the distance guard.

---

## Keepalive

When no data is pending, Alice sends periodic keepalive packets to:
1. Maintain the connection (detect dead tunnels)
2. Give Bob an opportunity to send data
3. Prevent NAT/firewall timeouts

Keepalive is a header-only packet with:
- Valid seq/ack/sack
- `FLAG_KEEPALIVE` set
- Zero segments

Idle poll-only packets use the keepalive flag. Empty responses that mean
"poll again soon" are not distinct from keepalive. Bob responds with a
keepalive-flag packet when idle and with queued data when available. If a
retransmit would exceed the per-request response cap, Bob responds with
KEEPALIVE + POLL_HINT (no segments) to signal pending data while keeping the
request/response contract. POLL_HINT is advisory and does not imply data was
sent. KEEPALIVE without POLL_HINT is a true idle keepalive. If either side has
actual data to send, the packet itself serves as keepalive—no channel 0
ping/pong messages are sent (legacy ping/pong are ignored if received).

For poll/keepalive decisions on Alice, `HAS_SEGMENTS` responses are treated as
real data (control or data segments) and `KEEPALIVE` responses are treated as
idle; POLL_HINT does not change this. Keepalive packets should not carry
segments.

Keepalive interval is configurable (default: 1.0 second).

Keepalive responses are suppressed when any channel data is queued; queued data
replaces the keepalive unless a retransmit is blocked by the per-request
response cap, in which case Bob uses KEEPALIVE + POLL_HINT.

---

## Poll Pacing (Alice)

When poll pacing is enabled, Alice spaces polls over time instead of sending
bursty send-and-drain cycles. After any send (data, keepalive, or retransmit),
the next poll slot is scheduled using:

```
interval = clamp(
    srtt_sec * tunnel_poll_rtt_ratio / max(target_inflight, 1),
    tunnel_poll_min_interval,
    min(tunnel_poll_max_interval, tunnel_keepalive_interval)
)
```

`target_inflight` is derived from the base inflight ratio (no ACK-rate feedback)
and clamped by the negotiated send window and transport max inflight. When no
SRTT sample exists, `srtt_sec` falls back to `tunnel_keepalive_interval`, and
SRTT is floored by `tunnel_pace_rtt_floor_ms`.

When poll pacing is disabled, Alice keeps the previous bursty poll behavior.
Keepalive remains the upper bound on poll spacing.

## Control Message Dispatch

Control messages arrive on channel 0 and are dispatched based on their type
field. See `doc/CONTROL_MESSAGES.md` for the message format specification.

### Dispatch Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Tunnel                                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Core Handlers (built-in)                  │  │
│  │  t="tun" ──▶ _handle_tunnel_message()                 │  │
│  │              (mtu, window)                            │  │
│  │  t="ch"  ──▶ channel_manager.handle_control_message() │  │
│  │              (open, close)                            │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Module Handlers (registered)              │  │
│  │  t="file" ──▶ FileModule.handle_message()             │  │
│  │  t="sh"   ──▶ ShellModule.handle_message()            │  │
│  │  t="sock" ──▶ SocksModule.handle_message()            │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Reserved Types

The tunnel owns two reserved message types that cannot be overridden:

| Type | Handler | Messages |
|------|---------|----------|
| `tun` | Tunnel | mtu, mtu_ok, mtu_ack, window, window_ok |
| `ch` | ChannelManager | open, open_ok, open_fail, close, close_ok, close_err, half_close |

The `half_close` channel message signals the end of a send stream while keeping
the receive side open until a full close handshake completes.

### Module Registration

Modules register handlers for their message types:

```python
class FileModule:
    TYPE = 'file'

    def __init__(self, tunnel):
        self._tunnel = tunnel
        tunnel.register_module(self.TYPE, self.handle_message)

    def handle_message(self, msg):
        cmd = msg.get('c')
        if cmd == 'get':
            self._handle_get(msg)
        elif cmd == 'put':
            self._handle_put(msg)
        # ...
```

Registration API:

```python
# Register a module handler
tunnel.register_module('file', file_module.handle_message)

# Unregister (e.g., when module is unloaded)
tunnel.unregister_module('file')
```

Constraints:
- Reserved types (`tun`, `ch`) cannot be registered
- Duplicate registration raises `ValueError`
- Module handler exceptions are caught and logged (don't crash tunnel)

### Bob Message Filtering

Bob only accepts control message types in its allowlist. By default this is
`tun` and `ch`. When a module handler is registered on Bob, explicitly allow
its type via `allow_message_type()` (or enable the module loader, which also
allows the `mod` type).

## MTU Handling

The tunnel negotiates per-direction packet MTUs. Each side proposes its
transport send and recv limits, and the negotiation clamps each direction
independently. The result is two packet MTUs:

- `send_packet_mtu`: max packet bytes this side may send.
- `recv_packet_mtu`: max packet bytes this side will accept.

| Direction | Packet MTU Used |
|-----------|-----------------|
| Alice -> Bob | Alice send_packet_mtu (Bob recv_packet_mtu) |
| Bob -> Alice | Bob send_packet_mtu (Alice recv_packet_mtu) |

Payload bytes are derived as `(packet_mtu - PACKET_HEADER_SIZE)`. The tunnel
passes that payload size to
`channel_manager.collect_segments(max_payload)` for outbound packets. Inbound
packets are validated against `recv_packet_mtu` (max packet size). The packet
header (38 bytes) is added on the wire.

Minimum packet MTU is `PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1`
(one segment header plus 1 byte of segment payload). Keepalive-only packets
are smaller but MTU definitions are segment-capable.

The `tun.mtu`/`tun.mtu_ok` control messages carry payload bytes, not packet
bytes. BaseTunnel converts between payload and packet MTUs by adding or
subtracting `PACKET_HEADER_SIZE`.

---

## Encryption

Only the packet body (segments) is encrypted. The header remains in the clear:

```
┌─────────────────────────────────────────┐
│ Header (clear)                          │
├─────────────────────────────────────────┤
│ cipher(segment1 + segment2 + ...)       │
└─────────────────────────────────────────┘
```

Supported ciphers:
- `Plain`: No encryption (passthrough, for testing only)
- `XOR`: Simple XOR with key (lightweight obfuscation)
- `RC4`: RC4 stream cipher

Cipher is configured at tunnel creation. Both sides must use the same cipher
and key. RC4 derives a per-packet key from (seq, direction) to keep retransmits
deterministic. Keystreams repeat if seq wraps under a static PSK.

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
- No successful response within `tunnel_no_response_timeout` seconds
- State transitions to CLOSED

Bob detects connection loss when:
- No request received within `tunnel_idle_timeout` seconds
- State transitions to CLOSED

---

## API

### AliceTunnel

```python
from sfb.config import Config
from sfb.crypto import RC4
from sfb.transport.dns import DnsClient
from sfb.tunnel import AliceTunnel

config = Config()
config.dns_base_domain = 'tunnel.example.com'
config.tunnel_keepalive_interval = 5.0

transport = DnsClient(config)
tunnel = AliceTunnel(
    transport=transport,
    config=config,
    crypto=RC4(key),
)

# Register module handlers (optional)
tunnel.register_module('file', file_module.handle_message)

# Connect with handshake
tunnel.connect(timeout=10.0)

# Main loop
while tunnel.connected:
    tunnel.tick()

    # Use channels (generic byte streams)
    channel = tunnel.channel_manager.open_channel()
    channel.wait_open(timeout=5.0)
    channel.write(b'Hello, world!')
    response = channel.read(4096, timeout=5.0)

tunnel.close()
```

### BobTunnel

```python
from sfb.config import Config
from sfb.crypto import RC4
from sfb.transport.dns import DnsServer
from sfb.tunnel import BobTunnel

config = Config()
config.dns_base_domain = 'tunnel.example.com'
config.tunnel_idle_timeout = 60.0

transport = DnsServer(config)
tunnel = BobTunnel(
    transport=transport,
    config=config,
    crypto=RC4(key),
)

# Register module handlers
tunnel.register_module('file', file_module.handle_message)
# Allow file control messages from Alice
tunnel.allow_message_type('file')

# Set up channel handler (optional)
def on_channel_request(channel_id):
    # Accept all channels (or add logic to reject)
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
| `tunnel_keepalive_interval` | 1.0s | Time between keepalive packets (Alice) |
| `tunnel_connect_timeout` | 10.0s | Handshake timeout (Alice) |
| `tunnel_idle_timeout` | 60.0s | Connection timeout with no activity (Bob) |
| `tunnel_no_response_timeout` | 60.0s | Alice timeout on response silence |
| `protocol_initial_rto_ms` | 1000ms | Initial retransmit timeout (Alice) |

---

## Thread Safety

The tunnel classes are **not thread-safe**. For multi-threaded use:
- Run the tunnel loop in a dedicated thread
- Use thread-safe queues for channel I/O
- Or use the channel's built-in threading primitives (events, locks)

---

## File Structure

```
sfb/tunnel/
├── __init__.py       # Exports AliceTunnel, BobTunnel, TunnelState, TunnelError
├── base_tunnel.py    # BaseTunnel with shared functionality
├── alice_tunnel.py   # AliceTunnel implementation
└── bob_tunnel.py     # BobTunnel implementation
```
