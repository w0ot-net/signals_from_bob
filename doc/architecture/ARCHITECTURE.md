# Architecture Overview

## Network Topology

```
ALICE (Inside DMZ)                              BOB (Outside)
┌─────────────────────┐                        ┌─────────────────────┐
│                     │                        │                     │
│   ┌─────────────┐   │      Covert Channel    │   ┌─────────────┐   │
│   │   Modules   │   │   (DNS/ICMP/etc)       │   │   Modules   │   │
│   │  - SOCKS    │   │                        │   │  - SOCKS    │   │
│   │  - Port Fwd │   │                        │   │  - Port Fwd │   │
│   │  - Files    │   │                        │   │  - Files    │   │
│   └──────┬──────┘   │                        │   └──────┬──────┘   │
│          │          │                        │          │          │
│   ┌──────┴──────┐   │                        │   ┌──────┴──────┐   │
│   │   Tunnel    │   │ ═══════════════════▶   │   │   Tunnel    │   │
│   │   Client    │   │     Alice initiates    │   │   Server    │   │
│   └─────────────┘   │                        │   └─────────────┘   │
│                     │                        │                     │
└─────────────────────┘                        └─────────────────────┘
```

Alice initiates all transport-level connections. Bob cannot reach Alice directly.

---

## Layer Stack

```
┌─────────────────────────────────────┐
│         Application Modules         │
│   (SOCKS, Port Forward, File Transfer)   │
└──────────────────┬──────────────────┘
                   │ channel read/write
┌──────────────────┴──────────────────┐
│         Channel Manager             │
│     (channel 0=control, 1-255)      │
└──────────────────┬──────────────────┘
                   │ segments
┌──────────────────┴──────────────────┐
│         Reliability Layer           │
│     (seq/ack, SACK, retransmit)     │
└──────────────────┬──────────────────┘
                   │ packets
┌──────────────────┴──────────────────┐
│           Crypto Layer              │
│     (none / xor / rc4 / sha256)     │
└──────────────────┬──────────────────┘
                   │ encrypted bytes
┌──────────────────┴──────────────────┐
│        Transport Abstraction        │
│         (DNS / ICMP / etc)          │
└─────────────────────────────────────┘
```

---

## Components

### Transport

Handles medium-specific encoding/decoding. Exposes a request/response interface:
Alice sends requests (optionally pipelined) and Bob responds.

Implemented transports:
- DNS
- ICMP (Linux-only)
- UDP ephemeral
- TLS handshake
- TLS handshake bump
- In-memory (tests/simulation)
- Lossy wrapper (impairs any transport for testing)

MTU negotiation is asymmetric. Initial send/recv MTUs come from transport
limits, with protocol_initial_packet_mtu as a fallback before MTU_OK.

### Crypto

PSK-based encryption. When enabled, the header stays in the clear and only
the packet body (segments) is encrypted before handoff to transport.

Modes: none, xor, rc4, sha256

Key: raw PSK bytes (non-empty)

Only one connection is active at a time, so the key is derived directly from
the PSK without connection-specific material.

### Reliability

- Assigns seq to outgoing packets
- Tracks acks and sack from peer
- Buffers unacked packets for retransmit
- Reorders incoming packets
- Retransmission is asymmetric: Alice uses RTT-based timers; Bob retransmits
  opportunistically on each poll (see PROTOCOL.md for details)

### Channel Manager

- Maintains channel table (id → Channel)
- Packs outgoing channel data into segments (round-robin)
- Routes incoming segments to channels
- Handles control messages on channel 0
- Allocates channel IDs (odd=Alice, even=Bob, 8-bit with wraparound)
  (see doc/architecture/CHANNEL_MANAGER.md for packing policy)

### Application Modules

Built on top of channels. Examples:

- **SOCKS proxy**: Bob runs SOCKS server, Alice relays connections
- **Port forward**: Bob listens locally, Alice connects to fixed targets
- **File transfer**: Upload/download files between Alice and Bob

### Platform Support

The project must run on both Windows and Linux.

---

## Data Flow: Bob → Alice (SOCKS/Port Forward example)

1. User connects to Bob's local listen port
2. Bob's SOCKS/port_fwd module opens channel 2, sends OPEN to Alice via channel 0
3. Alice connects to target, sends OPEN_OK
4. Data flows on channel 2
5. Either side closes channel when done

---

## Beaconing (Polling Transports)

Alice continuously polls Bob. Her polling strategy maximizes throughput when
Bob has data while minimizing overhead when idle.

**Polling behavior:**
- After receiving `HAS_SEGMENTS`: poll immediately (no delay)
- After receiving `KEEPALIVE`: wait for idle interval (e.g., 1-5s)

This ensures maximum throughput: if Bob has 10 packets queued, Alice drains
them as fast as the network allows. When Bob has nothing to send, he responds
with `KEEPALIVE` (zero segments) and Alice slows down.

---

## Connection Lifecycle

```
Alice                              Bob
  │                                  │
  │───────── SYN ───────────────────▶│
  │                                  │
  │◀──────── SYN+ACK ────────────────│
  │                                  │
  │───────── ACK ───────────────────▶│
  │                                  │
  │        ... normal data ...       │
  │                                  │
  │   (timeout: Alice=30 packets no  │
  │    response, Bob=60s silence)    │
  │                                  │
```

---

## File Structure

```
sfb/
├── __init__.py
├── compat.py                  # Python 2/3 compatibility
├── crypto.py                  # Cipher implementations
├── logging_util.py            # Logging utilities
├── control_message.py         # ControlMessage base + encode/validate
├── protocol/
│   ├── __init__.py
│   ├── constants.py           # Protocol constants
│   ├── packet.py              # Packet structure
│   └── segment.py             # Segment structure
├── reliability/
│   ├── __init__.py
│   ├── pacing.py              # Adaptive pacing (Alice)
│   ├── rtt.py                 # RTT estimation (Alice)
│   ├── send_window.py         # Send window management
│   └── recv_window.py         # Receive window, SACK
├── channel/
│   ├── __init__.py
│   ├── channel.py             # Channel class
│   ├── channel_control_messages.py # Channel control message helpers
│   ├── channel_manager.py     # Channel multiplexing
│   └── control_channel.py     # Control message helpers
├── transport/
│   ├── __init__.py
│   ├── transport_base.py      # Transport interface
│   └── dns/
│       ├── __init__.py
│       ├── codec.py           # Base32/64 encoding
│       ├── dns_client.py      # Alice's DNS client
│       └── dns_server.py      # Bob's DNS server
├── modules/
│   ├── __init__.py
│   ├── base_module.py
│   ├── relay_connection.py
│   ├── relay_control_messages.py
│   ├── relay_logging.py
│   ├── relay_pump.py
│   ├── file_transfer/
│   │   ├── __init__.py
│   │   ├── file_transfer_control_messages.py
│   │   └── file_transfer.py
│   ├── port_fwd/
│   │   ├── __init__.py
│   │   ├── port_fwd_server.py
│   │   └── port_fwd_relay.py
│   ├── socks/
│   │   ├── __init__.py
│   │   ├── socks_control_messages.py
│   │   ├── socks_server.py
│   │   └── socks_relay.py
│   └── nc_linux/
│       ├── __init__.py
│       ├── nc_linux.py
│       ├── nc_linux_control_messages.py
│       └── nc_linux_pump.py
└── tunnel/
    ├── __init__.py            # Exports AliceTunnel, BobTunnel
    ├── base_tunnel.py         # BaseTunnel with shared functionality
    ├── alice_tunnel.py        # AliceTunnel implementation
    ├── bob_tunnel.py          # BobTunnel implementation
    └── tunnel_control_messages.py  # Tunnel control message helpers
```
