# Segment Packing and Distribution

This document defines how the channel muxer distributes channel data into
packet segments. The goals are:

- Preserve channel 0 priority.
- Distribute non-zero channels evenly within each packet.
- Fill packets as close to MTU as possible.

---

## Definitions

- MTU: Maximum packet size, including the 8-byte packet header.
- Segment header: 3 bytes (channel + len).
- Remaining space: MTU minus packet header and any segments already added.
- Active channel: A channel with pending outbound data.

---

## Packing Rules

1. **Channel 0 priority**: If channel 0 has pending data, it is always serviced
   before any non-zero channel in each packet.
2. **Primary channel cap**: For each packet, choose the next non-zero channel in
   round-robin order as the primary channel. It may use up to 75% of the
   remaining payload space (after any channel 0 data).
3. **Round-robin fill**: After the primary allocation, add at most one segment
   from each remaining non-zero channel in round-robin order until the packet
   is full or no data remains.
4. **Segment slicing**: If a channel has more data than fits, split it across
   multiple segments and/or packets.
4. **Minimum segment size**: A segment requires at least 3 bytes header plus
   1 byte payload. If less space remains, stop packing.
5. **Segment slicing**: If a channel has more data than fits, split it across
   multiple segments and/or packets.

---

## Packing Algorithm (Normative)

Given an MTU and a set of per-channel outbound buffers:

1. Start a new packet with `used = 8` (packet header size).
2. If channel 0 has data, emit one segment from channel 0 first.
3. Compute `remaining = mtu - used`. If `remaining < 4`, stop.
4. Select the primary non-zero channel as the next in round-robin order among
   channels with pending data.
5. Allocate up to 75% of `remaining` (payload bytes only) to the primary:
   - `primary_cap = floor(remaining * 0.75)`
   - `primary_payload = min(pending, remaining - 3, primary_cap)`
   - Emit one segment for the primary channel, update `used`.
6. With the new `remaining`, iterate non-zero channels (starting after primary)
   in round-robin order, emitting at most one segment per channel until:
   - `remaining < 4`, or
   - no non-zero channel has pending data.
7. If space still remains, repeat step 6 (additional rounds) until the packet is
   full or no data remains.

---

## Notes

- Channel 0 is always serviced first, but it does not monopolize the packet
  unless it is the only active channel.
- The round-robin order should be stable to avoid bias (e.g., track the next
  channel to start with per packet).
- The 75% cap limits monopolization while keeping segment header overhead low.
- Additional rounds keep packets close to MTU without starving other channels.
