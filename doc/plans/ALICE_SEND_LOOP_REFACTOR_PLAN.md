# Alice Send Loop Refactor Plan

## Goal
- Reduce duplication in the Alice send loop by consolidating serial-window and
  normal-path send logic.
- Simplify pending-data checks by removing the separate control event and
  routing all pending queries through ChannelManager.has_pending_data(mode=...).
- Make the pending-data predicate and control-only selection explicit so pacing
  and keepalive behavior cannot drift between branches.

## Non-Goals
- Change send pacing, keepalive timing, retransmit rules, or window
  negotiation behavior.
- Alter _has_pending_data_acks semantics or idle-sleep behavior.
- Modify Bob-side logic or transport implementations.
- Update or run tests.

## Affected Components
- sfb/tunnel/alice_tunnel.py
- sfb/channel/channel_manager.py

## Plan
1) Remove ChannelManager.control_send_event and update has_pending_data() to
   accept a mode argument (mode='control_or_data'|'control'|'data'), where
   'control_or_data' matches current include_control=True semantics, 'control'
   checks only the control channel, and 'data' checks only non-control channels.
2) Update Alice send-loop logic to derive pending predicate and control_only
   from serial_window using has_pending_data(mode=...) so a single send path
   handles both cases without branching.
3) Replace keepalive suppression checks to use the same pending predicate from
   has_pending_data(mode='control_or_data') to keep behavior consistent with the
   send path.
4) Adjust any other call sites that reference control_send_event or
   has_pending_data(include_control=...) to the new mode API.
5) Review the diff to confirm behavior is unchanged aside from the API change
   and that the loop remains Python 2.7/3 compatible.
