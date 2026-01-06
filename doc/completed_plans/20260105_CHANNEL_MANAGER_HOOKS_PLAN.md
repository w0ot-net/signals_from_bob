# Channel Manager Hook And Packing Cleanup Plan

## Goal
- Add a remote channel request hook that can accept or reject incoming opens.
- Remove legacy keepalive_data segment packing path.
- Align CHANNEL_MANAGER.md with actual behavior (ID reuse scope, channel 0 exceptions).

## Non-Goals
- Change channel allocation rules or transport behavior.
- Add new module behaviors beyond the channel request hook.

## Affected Components
- sfb/channel/channel_manager.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- doc/CHANNEL_MANAGER.md
- doc/TUNNEL.md

## Plan
1) Add a channel request handler API on ChannelManager (setter + stored callback).
2) Update remote open handling to invoke the handler; send open_ok on accept and open_fail on reject or handler error.
3) Remove keepalive_data support from ChannelManager.collect_segments and tunnel _collect_segments signatures; update call sites to pass only max_payload and flags.
4) Update doc/CHANNEL_MANAGER.md to:
   - clarify ID reuse cooldown applies to locally owned IDs only,
   - note channel 0 is exempt from close_err responses,
   - remove keepalive_data wording.
5) Verify doc/TUNNEL.md still matches the new hook API; update if needed.

## Execution Notes
- Added channel request handler support with open_fail on rejection or handler error.
- Removed keepalive_data packing path and updated segment collection signatures.
- Updated doc/CHANNEL_MANAGER.md for ID reuse scope and channel 0 close_err behavior.
- Verified doc/TUNNEL.md already reflects the hook API; no changes needed.
