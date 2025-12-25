# Architecture Overview

## Network Topology

```
ALICE (Inside DMZ)                              BOB (Outside)
┌─────────────────────┐                        ┌─────────────────────┐
│                     │                        │                     │
│   ┌─────────────┐   │      Covert Channel    │   ┌─────────────┐   │
│   │   Modules   │   │   (DNS/ICMP/etc)       │   │   Modules   │   │
│   │  - Relay    │   │                        │   │  - SOCKS    │   │
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
│       (SOCKS, File Transfer)        │
└──────────────────┬──────────────────┘
                   │ channel read/write
┌──────────────────┴──────────────────┐
│           Channel Muxer             │
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
│        (none / xor / rc4)           │
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

Handles medium-specific encoding/decoding. Exposes simple send/recv interface.

Polling transports (DNS, ICMP): Alice beacons continuously, Bob responds.

TLS-handshake transport is future-only and not part of the current
architecture.

### Crypto

PSK-based encryption. When enabled, the entire packet is encrypted before
handoff to transport.

Modes: none, xor, rc4

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

### Channel Muxer

- Maintains channel table (id → buffer)
- Packs outgoing channel data into segments
- Routes incoming segments to channels
- Handles control messages on channel 0
  (see doc/SEGMENT_PACKING.md for packing policy)

### Application Modules

Built on top of channels. Examples:

- **SOCKS proxy**: Bob runs SOCKS server, Alice relays connections
- **File transfer**: Upload/download files between Alice and Bob

### Platform Support

The project must run on both Windows and Linux.

---

## Data Flow: Bob → Alice (SOCKS example)

1. User connects to Bob's SOCKS port
2. Bob's SOCKS module opens channel 2, sends OPEN to Alice via channel 0
3. Alice connects to target, sends OPEN_OK
4. SOCKS data flows on channel 2
5. Either side closes channel when done

---

## Beaconing (Polling Transports)

Alice continuously polls Bob. Adaptive interval:

- **Idle**: slow polling (e.g., 1-5s)
- **Active**: fast polling (e.g., 100ms)

Transition:
- Idle → Active: received packet with non-pong data
- Active → Idle: N consecutive pong-only packets

Bob signals "nothing to send" via `{"cmd":"pong"}` control message. Alice uses
this to slow down.

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
tunnel/
├── protocol.py        # Packet/segment structures, constants
├── crypto.py          # Cipher implementations
├── reliability/       # Seq/ack, SACK, retransmit logic
├── muxer.py           # Channel multiplexing
├── transport/
│   ├── transport_base.py        # Transport interface
│   ├── dns.py
│   ├── icmp.py        # (future)
│   └── tls.py         # (future)
├── modules/
│   ├── socks.py       # SOCKS5 proxy
│   └── files.py       # File transfer
├── alice.py           # Client entrypoint
└── bob.py             # Server entrypoint
```
