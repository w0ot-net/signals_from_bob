# DNS Fixed Clamp Policy Phase 1 - Protocol and Tunnel

Status: completed

## Summary
Remove POLL_HINT from the protocol and tunnel implementation. This is a
breaking change; both sides must upgrade together.

## Dependencies
- Recommended order: run this phase before Phase 2 and Phase 3 so code and docs
  do not mix old flags with the new fixed-cap DNS behavior.

## Goals
- Remove the POLL_HINT flag from protocol constants and exports.
- Remove poll-hint checks and logging from the tunnel base.
- Stop Bob from emitting poll-hint behavior in responses.

## Non-Goals
- DNS clamp changes (Phase 2).
- Documentation updates (Phase 3).
- Any behavior changes in non-DNS transports beyond flag removal.

## Affected Components
- sfb/protocol/constants.py
- sfb/protocol/packet.py
- sfb/protocol/__init__.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py

## Plan
1. Protocol constants and exports
   - Delete FLAG_POLL_HINT from constants and ensure bit 4 is reserved.
   - Remove FLAG_POLL_HINT imports/exports from sfb/protocol/__init__.py.

2. PacketHeader cleanup
   - Remove poll_hint_flag property and setter from PacketHeader.
   - Remove POLL_HINT from _VALID_FLAGS.
   - Update PacketHeader docstrings and __repr__ to omit POLL_HINT.

3. BaseTunnel validation and logging
   - Remove poll_hint fields from tunnel.packet_send and tunnel.packet_recv logs.
   - Simplify _validate_content_flags by removing poll-hint checks and
     poll_hint-specific protocol violation reasons.
   - Rely on PacketHeader._validate_flags to reject legacy POLL_HINT packets.

4. BobTunnel response path cleanup
   - Remove OR-ing POLL_HINT on retransmit, keepalive, and segments responses.
   - Remove poll_hint fields from decision dictionaries and log payloads.
   - Rename poll-hint-specific drop/log reasons (for example,
     poll_hint_window_full) to neutral keepalive/window_full reasons.
   - Update responder contexts and log details to avoid poll-hint references.

5. Sanity sweep
   - Search for remaining POLL_HINT or poll_hint references in tunnel code and
     remove any leftover imports, log fields, or decision keys.

## Testing
- Do not run tests.

## Execution Notes (20260111)
- Removed FLAG_POLL_HINT from protocol constants, PacketHeader validation, and
  protocol exports; marked bit 4 reserved.
- Dropped poll-hint validation/log fields in BaseTunnel and removed poll-hint
  response behavior from Bob.
- Removed DNS client poll-hint clamp state to keep call sites consistent with
  the flag removal.
- Tests not run (per plan).
