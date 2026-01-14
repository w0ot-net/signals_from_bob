# Channel Control ch=0 Fatal Plan

Status: draft

## Goal

Enforce the protocol invariant that any channel control message (t="ch")
targeting ch=0 is a fatal protocol error, so the tunnel logs, drops the
message, and closes immediately instead of warning and ignoring it.

## Affected Components

- sfb/tunnel/base_tunnel.py
- sfb/channel/channel_manager.py
- doc/architecture/CONTROL_MESSAGES.md
- doc/architecture/PROTOCOL.md

## Design Notes

- Centralize validation in tunnel control dispatch so channel_manager only sees
  valid channel messages.
- Treat any t="ch" with ch=0 as a protocol violation regardless of command.
- Use _close_protocol_violation() for consistent logging and shutdown.
- Keep behavior deterministic and fail fast; avoid silent ignores.

## Implementation Steps

1. Add a guard in tunnel control handling (prefer _handle_channel_message) to
   check msg.get('ch') == 0 for all t="ch" commands and call
   _close_protocol_violation with a specific reason that includes cmd/ch in
   logging context.
2. Ensure channel_manager no longer warns and ignores ch=0 messages; either
   remove _reject_control_channel usage or convert it to an internal assert
   path that should be unreachable after the tunnel guard.
3. Update CONTROL_MESSAGES and PROTOCOL docs to state that any t="ch" targeting
   ch=0 is fatal and enforced at dispatch time.

## Validation

- Add or update unit coverage for channel control dispatch if needed.
- Do not run tests in tests/e2e/.
