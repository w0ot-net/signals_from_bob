# MTU Ack Pending Invariant Plan

Status: complete

## Goal

Require a pending send MTU increase before accepting `mtu_ack` so unexpected
acks are treated as protocol violations instead of silently marking MTU
negotiated.

## Affected Components

- sfb/tunnel/base_tunnel.py
- doc/architecture/PROTOCOL.md
- doc/architecture/CONTROL_MESSAGES.md

## Design Notes

- Treat `mtu_ack` without `_pending_send_packet_mtu` as a fatal protocol error.
- Keep the invariant in the tunnel handler so it is enforced uniformly for
  all transports.
- Prefer closing immediately over clamping or ignoring to surface bugs early.

## Implementation Steps

1. In `_handle_mtu_ack`, verify `_pending_send_packet_mtu` is not `None`
   before applying; otherwise call `_close_protocol_violation` with a specific
   reason and return.
2. Update protocol docs to state `mtu_ack` is only valid after a pending send
   MTU increase is staged.
3. Update control-message docs to match the protocol constraint.

## Validation

- Add or update unit coverage for MTU negotiation if needed.
- Do not run tests in tests/e2e/.

## Execution Notes (2026-01-14)

- Closed on mtu_ack without a pending send MTU increase.
- Updated protocol and control-message docs to require a pending increase.
