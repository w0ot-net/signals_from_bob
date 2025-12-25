# Segment Packing and Distribution

This document defines how the channel muxer distributes channel data into
packet segments. The goals are:

- Preserve channel 0 priority, while suppressing keepalives when data exists.
- Minimize segment header overhead for efficiency.
- Fill packets as close to MTU as possible.

---

## Definitions

- MTU: Maximum packet size, including the 8-byte packet header.
- Segment header: 3 bytes (channel + len).
- Remaining space: MTU minus packet header and any segments already added.
- Active channel: A channel with pending outbound data.
- Keepalive: `ping` or `pong` control message on channel 0.

---

## Packing Rules

1. **Channel 0 priority**: If channel 0 has pending non-keepalive data, it is
   always serviced before any non-zero channel in each packet.
2. **Keepalive suppression**: Do not include `ping` or `pong` in a packet that
   already carries any other channel data.
3. **Primary channel fill**: For each packet, choose the next non-zero channel in
   round-robin order as the primary channel. It may use all remaining payload
   space (after any channel 0 data).
4. **Round-robin fill**: Only if the primary channel lacks enough data to fill
   the packet, add segments from other non-zero channels in round-robin order
   until the packet is full or no data remains.
5. **Minimum segment size**: A segment requires at least 3 bytes header plus
   1 byte payload. If less space remains, stop packing.
6. **Segment slicing**: If a channel has more data than fits, split it across
   multiple segments and/or packets.

---

## Packing Algorithm (Normative)

Given an MTU and a set of per-channel outbound buffers:

1. Start a new packet with `used = 8` (packet header size).
2. If channel 0 has non-keepalive data, emit one segment from channel 0 first.
3. Compute `remaining = mtu - used`. If `remaining < 4`, stop.
4. Select the primary non-zero channel as the next in round-robin order among
   channels with pending data.
5. Allocate as much of `remaining` as possible to the primary:
   - `primary_payload = min(pending, remaining - 3)`
   - Emit one segment for the primary channel, update `used`.
6. If the primary channel did not fill the packet, iterate the other non-zero
   channels (starting after primary) in round-robin order, emitting at most one
   segment per channel until:
   - `remaining < 4`, or
   - no non-zero channel has pending data.
7. If space still remains, repeat step 6 (additional rounds) until the packet is
   full or no data remains.
8. If no non-zero channels have pending data and channel 0 only has keepalive
   data, emit a `ping` or `pong` segment on channel 0.

---

## Notes

- Channel 0 is always serviced first, but it does not monopolize the packet
  unless it is the only active channel.
- The round-robin order should be stable to avoid bias. Advance the pointer to
  the next non-zero channel after the primary, even if other channels were used
  to fill the packet.
- The primary channel only yields space when it cannot fill the packet, which
  minimizes segment header overhead.
- Additional rounds keep packets close to MTU when the primary channel is short.
