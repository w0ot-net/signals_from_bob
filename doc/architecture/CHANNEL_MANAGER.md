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
- Closed IDs allocated by the local side are not reused until
  `channel_id_reuse_cooldown` seconds elapse; remote-owned IDs are not tracked
  for reuse cooldown.
- If all IDs are in use, allocation fails with an error.

---

## Control Channel

Channel 0 is created at initialization and is always open. It carries control
messages encoded as JSON lines (see `doc/architecture/PROTOCOL.md`).

Channel 0 handling is special in segment packing:
- Control data is always prioritized.
- Keepalive traffic is handled at the packet header level.

The control channel exposes its own `send_event` for queued control data.
ChannelManager tracks non-control pending data separately using an internal
data-pending event. `has_pending_data()` derives combined state by OR-ing the
control `send_event` with the data-pending event, so control drain does not
clear pending data-channel state.

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

- Local close marks state `CLOSING`; `close` is sent after the send buffer drains.
- Local abort sends `close_err` immediately, drops buffered data, and removes the channel.
- Remote `close` triggers `close_ok` and removes the channel.
- Remote `close_err` closes the channel with error, drops buffers, sends `close_ok`,
  and removes the channel.
- Remote `close_ok` removes the channel if it was closing.
- `open_fail` sets state to `CLOSED` and removes the channel.

### Half-Close

- Local `close_write()` marks the send side closed; `half_close` is sent after
  the send buffer drains.
- Remote `half_close` marks the receive side closed and keeps the channel
  registered for continued outbound sends.
- No automatic close occurs when both halves are closed; a full close handshake
  (`close`/`close_ok`) is still required.

---

## Incoming Segments

Incoming segments are routed by channel ID:
- If the channel exists, the data is delivered to it.
- Unknown channel IDs are dropped and trigger a `close_err` back to the peer,
  rate-limited per channel ID; channel 0 is exempt from `close_err` responses.
- If delivery would exceed a channel's receive buffer, the channel is aborted
  with `close_err`.
- Control segments from a packet are delivered and processed before any data
  segments in that packet so open/close state changes apply before data.

---

## Outgoing Segment Packing

The manager collects segments from channels to fit within the provided
`max_payload` size. The size includes segment headers (3 bytes each).
If less than 4 bytes remain (header + 1 byte payload), packing stops.

### Packing Rules

1. **Channel 0 priority**: If channel 0 has data, emit it first.
2. **Primary channel fill**: Choose a primary non-zero channel in round-robin
   order and give it as much remaining space as possible.
3. **Round-robin fill**: If space remains, take at most one segment from each
   other active channel, starting after the primary, until full.

### Round-Robin Pointer

Active channels are stored in order. After selecting the primary channel, the
manager moves it to the tail so the next channel becomes primary on the next
packet. This avoids bias across packets.

### Segment Slicing

If a channel has more data than fits:
- The manager takes a slice that fits in the current packet.
- Remaining data stays queued for later packets.

## Drain Stats

Per-channel drain stats (`channel.drain`) are collected only when verbose
stats are enabled (`-v`) and debug logging is active.

---

## Concurrency

Channel registration and lookup are protected by a lock. Segment collection
reads the active-channel list under the lock and then accesses channel queues
outside the lock, matching the current implementation. Channels use
independent send/recv locks; the manager does not hold its lock while calling
channel methods, and it relies on send-state callbacks (invoked outside
channel locks) to track active channels and avoid scanning on every tick.

The data-pending event is updated under the same lock whenever the active set
changes so pending-data checks can avoid lock acquisition in hot loops.
