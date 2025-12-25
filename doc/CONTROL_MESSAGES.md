# Control Messages

Control messages are JSON objects sent on channel 0. They coordinate actions
such as opening channels, negotiating parameters, and module-specific commands.

---

## Encoding and Framing

- Encoding: ASCII JSON
- One message per line
- Each message ends with a newline (`\n`)
- Multiple messages can be sent in a single segment or packet

Receivers parse channel 0 by buffering bytes until a newline is found, then
decoding a complete JSON object. Invalid JSON should be rejected and the
message dropped.

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

## Common Messages

This document only defines framing and chunking. Message definitions are in:

- `doc/PROTOCOL.md` (handshake, MTU, window, ping/pong, open/close)
- `doc/FILE_TRANSFER.md` (file module commands)
