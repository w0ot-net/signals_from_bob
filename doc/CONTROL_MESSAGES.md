# Control Messages

Control messages are JSON objects sent on channel 0. They coordinate tunnel
operations, channel lifecycle, and module-specific commands.

---

## Encoding and Framing

- Encoding: ASCII JSON, compacted (no extra whitespace)
- One message per line
- Each message ends with a newline (`\n`)
- Multiple messages can be sent in a single segment or packet
- Maximum message length: 0x1000 bytes (excluding the newline); longer lines
  are rejected

Receivers parse channel 0 by buffering bytes until a newline is found, then
decoding a complete JSON object. Invalid JSON should be logged and dropped.

---

## Chunking Across MTU

Control messages may be larger than the available MTU. Channel 0 supports
chunking:

- A control message may be split across multiple channel 0 segments.
- Those segments may appear in multiple packets if needed.
- The receiver must buffer partial data until the terminating newline arrives.
- Only when a full line is assembled should it be parsed as JSON.

This allows large control messages without changing the framing rules.

---

## Message Format

All control messages are JSON objects with the following fields:

| Field | Required | Description |
|-------|----------|-------------|
| `t`   | Yes      | Message type (string) - identifies which layer handles the message |
| `c`   | Yes      | Command (string) - the specific operation within that type |

Additional fields depend on the specific command.

Implementation note: use the `ControlMessage` base class in
`sfb/control_message.py` and the helpers in
`sfb/tunnel/tunnel_control_messages.py` to build and encode messages with
required `t` and `c` fields.

### Example Messages

```json
{"t":"tun","c":"ping"}
{"t":"tun","c":"mtu","tx":500,"rx":150}
{"t":"ch","c":"open","ch":2}
{"t":"ch","c":"close","ch":2}
{"t":"file","c":"get","rid":1,"ch":4,"path":"/etc/passwd"}
{"t":"sh","c":"open","ch":6,"rows":24,"cols":80}
```

---

## Type Registry

Message types are organized into two categories: reserved types (handled by
the tunnel core) and module types (handled by registered modules).

### Reserved Types

These types are built-in and cannot be overridden by modules.

| Type | Layer | Description |
|------|-------|-------------|
| `tun` | Tunnel | Keepalive, MTU/window negotiation |
| `ch` | Channel | Channel open/close lifecycle |

### Module Types

Modules register handlers for their message types. The tunnel dispatches
messages to the appropriate module based on the `t` field.

| Type | Module | Description |
|------|--------|-------------|
| `file` | File Transfer | List, get, put files |
| `sock` | SOCKS Proxy | SOCKS5 proxy control |
| `sh` | Shell | Interactive shell sessions |
| `fwd` | Port Forward | TCP port forwarding (future) |

New modules should choose a short, unique type code (2-4 characters).

---

## Dispatch Rules

When a control message is received:

1. Parse JSON and extract `t` field
2. Dispatch based on type:
   - `tun`: Tunnel handles internally (ping/pong, negotiation)
   - `ch`: Channel manager handles (open/close)
   - Other: Dispatch to registered module handler
3. Unknown types are logged and dropped

```
┌─────────────────────────────────────────────────────────┐
│                    Control Message                       │
│                  {"t":"X", "c":"Y", ...}                │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
                    ┌───────────┐
                    │  t = ?    │
                    └─────┬─────┘
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
     ┌─────────┐    ┌───────────┐   ┌───────────┐
     │ t="tun" │    │  t="ch"   │   │  t=other  │
     └────┬────┘    └─────┬─────┘   └─────┬─────┘
          │               │               │
          ▼               ▼               ▼
     ┌─────────┐    ┌───────────┐   ┌───────────┐
     │ Tunnel  │    │  Channel  │   │  Module   │
     │ Handler │    │  Manager  │   │  Handler  │
     └─────────┘    └───────────┘   └───────────┘
```

---

## Tunnel Messages (t="tun")

Tunnel-level messages handle keepalive and parameter negotiation.

### ping / pong

Keepalive messages. Alice sends `ping` when idle; Bob responds with `pong`.

```json
{"t":"tun","c":"ping"}
{"t":"tun","c":"pong"}
```

If either side has actual data to send, the packet itself serves as keepalive.
Ping/pong are only sent when no other data is pending.

### mtu / mtu_ok

MTU negotiation. Sent immediately after handshake.

```json
{"t":"tun","c":"mtu","tx":500,"rx":150}
{"t":"tun","c":"mtu_ok","tx":150,"rx":500}
```

Alice proposes her transport's max payload MTUs for each direction.
Bob responds with independent `tx`/`rx` values based on his limits.

Until `mtu_ok` is received, both sides limit packets to 100 bytes.

### window / window_ok

Send window negotiation. Sent after MTU negotiation.

```json
{"t":"tun","c":"window","size":64}
{"t":"tun","c":"window_ok","size":8}
```

Alice proposes max in-flight packets. Bob responds with
`min(alice_size, bob_max, 64)`. Maximum is 64 (SACK bitmap size).

Until `window_ok` is received, both sides use max_in_flight = 1.

---

## Channel Messages (t="ch")

Channel messages manage the lifecycle of data channels.

### open

Request to open a channel. Channels are generic bidirectional byte streams;
application-specific data (like connection targets) is negotiated separately
by the module using the channel.

```json
{"t":"ch","c":"open","ch":2}
```

| Field | Description |
|-------|-------------|
| `ch` | Channel ID (odd=Alice, even=Bob) |

### open_ok

Channel opened successfully.

```json
{"t":"ch","c":"open_ok","ch":2}
```

### open_fail

Channel open failed.

```json
{"t":"ch","c":"open_fail","ch":2,"reason":"connection refused"}
```

### close

Request to close a channel.

```json
{"t":"ch","c":"close","ch":2}
```

### close_ok

Channel closed.

```json
{"t":"ch","c":"close_ok","ch":2}
```

---

## Module Messages

Module-specific messages are documented in their respective files:

- **File Transfer** (`t="file"`): See `doc/FILE_TRANSFER.md`
- **SOCKS Proxy** (`t="sock"`): See `doc/MODULES.md#socks-proxy-module`
- **Shell** (`t="sh"`): See `doc/MODULES.md#shell-module-future`

### Module Message Guidelines

When defining messages for a new module:

1. Choose a short type code (2-4 chars, lowercase)
2. Use short field names to minimize overhead
3. Include `ch` field when the message relates to a specific channel
4. Define both request and response messages
5. Include error responses with `reason` field

Example module message pattern:

```json
{"t":"mymod","c":"start","ch":4,"param":"value"}
{"t":"mymod","c":"start_ok","ch":4}
{"t":"mymod","c":"err","ch":4,"reason":"invalid param"}
```

---

## Priority

Channel 0 segments containing control messages MUST be transmitted before
other channel data when multiple segments are queued in the same packet.

Within channel 0, messages are processed in order received.

---

The handshake uses packet header flags (SYN/ACK), not control messages.
