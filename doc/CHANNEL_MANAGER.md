# Channel Manager

This document describes the channel manager behavior and its segment packing
policy. It reflects the implementation in `sfb/channel/channel_manager.py`.

---

## Responsibilities

- Maintain the registry of active channels.
- Allocate channel IDs (odd for Alice, even for Bob).
- Route incoming segments to the correct channel.
- Collect outgoing channel data into packet segments.
- Handle channel lifecycle control messages.

---

## Channel IDs

- Channel IDs are 8-bit (1-255 for data channels).
- Channel 0 is reserved for control.
- Alice allocates odd IDs: 1, 3, 5, ...
- Bob allocates even IDs: 2, 4, 6, ...
- Allocation wraps around and skips IDs still in use.
- If all IDs are in use, allocation fails with an error.

---

## Control Channel

Channel 0 is created at initialization and is always open. It carries control
messages encoded as JSON lines (see `doc/PROTOCOL.md`).

Channel 0 handling is special in segment packing:
- Non-keepalive control data is always prioritized.
- Keepalives are suppressed if any other data is sent.

---

## Lifecycle

### Local Open

1. Allocate a channel ID for the local side.
2. Create a `Channel` in `OPENING` state.
3. Emit `{"t":"ch","c":"open", ...}` on channel 0.

### Remote Open

1. Receive `open` control message.
2. Validate that the channel ID belongs to the remote side.
3. Invoke the `on_channel_request` handler if present.
4. If accepted:
   - Create `Channel` in `OPEN` state.
   - Emit `open_ok`.
5. If rejected:
   - Emit `open_fail` with reason.

### Close

- Local close sends `close` and marks state `CLOSING`.
- Remote `close` triggers `close_ok` and removes the channel.
- Remote `close_ok` removes the channel if it was closing.
- `open_fail` sets state to `CLOSED` and removes the channel.

---

## Incoming Segments

Incoming segments are routed by channel ID:
- If the channel exists, the data is delivered to it.
- Unknown channel IDs are ignored.

---

## Outgoing Segment Packing

The manager collects segments from channels to fit within the provided
`max_payload` size. The size includes segment headers (3 bytes each).
If less than 4 bytes remain (header + 1 byte payload), packing stops.

### Packing Rules

1. **Channel 0 priority**: If channel 0 has non-keepalive data, emit it first.
2. **Primary channel fill**: Choose a primary non-zero channel in round-robin
   order and give it as much remaining space as possible, unless there is
   enough room for all active channels to send at least 1 byte each (see below).
3. **Round-robin fill**: If space remains, take at most one segment from each
   other active channel, starting after the primary, until full.
4. **Keepalive suppression**: Only emit keepalive data if no other segments
   were added to the packet.

### Fairness Cap (Per-Packet)

If there is enough space for every active non-zero channel to send at least
1 byte (payload + header), the packer caps each channel's segment so it leaves
room for the remaining active channels to send at least 1 byte in the same
packet. This preserves the round-robin order while preventing a single channel
from consuming the entire packet when others are active.

### Round-Robin Pointer

The round-robin pointer advances after selecting the primary channel, even if
other channels are used to fill the packet. This avoids bias across packets.

### Segment Slicing

If a channel has more data than fits:
- The manager takes a slice that fits in the current packet.
- Remaining data stays queued for later packets.

---

## Concurrency

Channel registration and lookup are protected by a lock. Segment collection
reads channel state under the lock and then accesses channel queues outside
the lock, matching the current implementation.
