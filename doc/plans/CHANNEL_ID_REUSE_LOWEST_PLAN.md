# Channel ID Lowest-Available Reuse Plan

Status: draft

## Summary
Change channel ID allocation to reuse the lowest available ID for the local
side instead of incrementing, so freed IDs are immediately reused (for
example, channel 2 closes and the next open uses channel 2).

## Goals
- Allocate the lowest free odd/even channel ID for each open.
- Reuse recently closed IDs immediately when configured (cooldown 0) and
  before higher IDs.
- Preserve channel parity ownership and existing open/close semantics.

## Non-Goals
- Protocol changes (no channel generation/epoch field).
- Changes to the open/close handshake.
- Tests or e2e updates.

## Affected Components
- `sfb/channel/channel_manager.py`
- `sfb/config.py`
- `doc/architecture/CHANNEL_MANAGER.md`

## Plan
1. Update channel ID allocation:
   - Replace the round-robin `_next_channel_id` allocator with a
     lowest-available scan from the side base (1 or 2) to 255 in steps of 2.
   - Keep honoring `channel_id_reuse_cooldown` by skipping IDs still in
     cooldown.
   - Remove `_next_channel_id` state and related wraparound logic.
2. Adjust reuse cooldown defaults:
   - Set `Config.channel_id_reuse_cooldown` default to `0.0` so immediate
     reuse is the default behavior and matches the requirement example.
   - Keep the cooldown check for deployments that choose a non-zero value.
3. Update documentation:
   - Document the lowest-available allocation policy in
     `doc/architecture/CHANNEL_MANAGER.md`.
   - Note that non-zero cooldown delays reuse and can be raised if stale
     packets are a concern.

## Commentary
This plan has merit for predictable ID reuse and compact channel numbering,
especially in long-lived sessions that open and close channels frequently.
The tradeoff is higher exposure to late packets landing on a reused ID, so
keeping the cooldown configurable (and documenting when to raise it) keeps
the risk manageable.

## Testing
- Do not run tests.
