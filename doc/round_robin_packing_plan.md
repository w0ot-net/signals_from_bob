# Round Robin Segment Packing Plan

## Summary
Simplify `ChannelManager.collect_segments()` to a pure round-robin packer so
no single busy channel can starve interactive channels. If the primary channel
has no data it is skipped, and if the primary cannot fill the packet, we take
data from the next channels in order until the packet is full or no data
remains.

## Goals
- Ensure fair per-packet segment selection across active channels.
- Avoid multi-second drain gaps for interactive channels under load.
- Keep control channel priority and keepalive behavior unchanged.

## Affected Components
- `sfb/channel/channel_manager.py` (segment packing logic)
- `doc/CHANNEL_MANAGER.md` (packing rules documentation)
- `tests/test_channel.py` (collect_segments coverage)

## Plan
1. Review current `collect_segments()` flow and document the exact behaviors
   that cause starvation (primary fill and sparse round-robin).
2. Implement a single round-robin packing loop:
   - Always pack channel 0 first when it has data.
   - Snapshot active channel order from `_active_channels`.
   - Start from the current primary (head of the list).
   - Iterate channels in order, skipping any channel that yields no data.
   - When a channel contributes data, move it to the tail (advance pointer).
   - Continue looping while there is space for another segment and at least one
     channel produced data in the last pass.
3. Decide and document the data-less policy:
   - If `_take_send_data()` returns empty for a channel, do not move it to the
     tail; leave it in place so the next active channel can be considered.
   - If a channel is missing from the snapshot, remove it from
     `_active_channels` (existing cleanup behavior).
4. Update `doc/CHANNEL_MANAGER.md` to describe the new round-robin rules,
   including the skip-empty-primary and multi-channel fill behavior.
5. Update tests in `tests/test_channel.py`:
   - Add a test that an empty primary is skipped.
   - Add a test that multiple channels can fill a single packet in order.
   - Ensure control channel priority and keepalive suppression still hold.

## Success Criteria
- SSH sessions receive regular drain opportunities while a large download is
  active.
- `tests/test_channel.py` validates the new round-robin behavior.
