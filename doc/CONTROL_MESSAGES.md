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
{"t":"tun","c":"mtu","tx":500,"rx":150}
{"t":"ch","c":"open","ch":2}
{"t":"ch","c":"half_close","ch":2}
{"t":"ch","c":"close","ch":2}
{"t":"ch","c":"close_err","ch":2,"code":"aborted","reason":"Channel aborted"}
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
| `tun` | Tunnel | MTU/window negotiation (keepalive is a header flag) |
| `ch` | Channel | Channel open/close lifecycle |

### Module Types

Modules register handlers for their message types. The tunnel dispatches
messages to the appropriate module based on the `t` field.

| Type | Module | Description |
|------|--------|-------------|
| `file` | File Transfer | List, get, put files |
| `sock` | SOCKS Proxy | SOCKS5 proxy control |
| `nc` | NC Linux | Bind a channel to a Linux file descriptor |
| `sh` | Shell | Interactive shell sessions |
| `fwd` | Port Forward | TCP port forwarding |

New modules should choose a short, unique type code (2-4 characters).

---

## Dispatch Rules

When a control message is received:

1. Parse JSON and extract `t` field
2. Dispatch based on type:
   - `tun`: Tunnel handles internally (negotiation; keepalive is a header flag;
     legacy ping/pong ignored)
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

Tunnel-level messages handle parameter negotiation. Keepalive is encoded
as a packet header flag (see `doc/PROTOCOL.md`).

### Legacy ping / pong (ignored)

Older clients may send `ping`/`pong` control messages. These are ignored, and
the current tunnel implementation does not emit them.

### mtu / mtu_ok

MTU negotiation. Sent immediately after handshake.

```json
{"t":"tun","c":"mtu","tx":500,"rx":150}
{"t":"tun","c":"mtu_ok","tx":150,"rx":500}
```

Alice proposes her transport's max payload MTUs for each direction.
Bob responds with independent `tx`/`rx` values based on his limits.

Until `mtu_ok` is received, both sides limit payloads to 100 bytes (header added on the wire).

### window / window_ok

Send window negotiation. Sent after MTU negotiation.

```json
{"t":"tun","c":"window","size":256}
{"t":"tun","c":"window_ok","size":8}
```

Alice proposes max in-flight packets. Bob responds with
`min(alice_size, bob_max, 256)`. Maximum is 256 (SACK bitmap size).

Until `window_ok` is received, both sides use max_in_flight = 1.

---

## Channel Messages (t="ch")

Channel messages manage the lifecycle of data channels.

### Channel 0 Constraint

Channel 0 is reserved for control messages and is always open. Any channel
message (`t="ch"`) that targets `ch=0` is a fatal protocol error: log, drop the
message, and close the tunnel.

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

### close_err

Channel closed with error (abort).

```json
{"t":"ch","c":"close_err","ch":2,"code":"recv_overflow","reason":"Receive buffer overflow"}
```

Receiver responds with `close_ok` and drops buffered data for the channel.

| Field | Description |
|-------|-------------|
| `code` | Error code (string) |
| `reason` | Error message (string) |

### half_close

Sender will not send more data on this channel (half-close).

```json
{"t":"ch","c":"half_close","ch":2}
```

Receiver treats this as remote send closed and returns `b''` on read once its
receive buffer drains. The channel remains open for sending until it is fully
closed with `close`/`close_ok`.

---

## Module Messages

Module-specific messages are documented in their respective files:

- **File Transfer** (`t="file"`): See `doc/FILE_TRANSFER.md`
- **SOCKS Proxy** (`t="sock"`): See `doc/MODULES.md#socks-proxy-module`
- **Port Forward** (`t="fwd"`): See `doc/PORT_FWD.md`
- **Shell** (`t="sh"`): See `doc/MODULES.md#shell-module-future`

### Port Forward Messages (t="fwd")

Port forwarding negotiates the target over control messages, then uses the
channel for bidirectional data flow.

```json
{"t":"fwd","c":"connect","rid":1,"ch":2,"host":"example.com","port":443}
{"t":"fwd","c":"connect_ok","rid":1,"ch":2}
{"t":"fwd","c":"err","rid":1,"ch":2,"code":"refused","reason":"connection refused"}
```

Fields:
- `rid`: Request ID for correlation
- `ch`: Channel ID opened by Bob
- `host`: Target hostname/IP (connect only)
- `port`: Target port (connect only)
- `bhost`: Bound host on Alice (connect_ok only, optional)
- `bport`: Bound port on Alice (connect_ok only, optional)
- `code`: Error code (err only)
- `reason`: Human-readable reason (err only)

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
