# Alice Poll Keepalive Plan

Status: draft

## Goal

Ensure Alice never emits empty packets without FLAG_KEEPALIVE, align poll
behavior with the intended keepalive semantics, keep pending-data ACK tracking
from being held open by keepalives, and avoid recording unacked sends when the
transport send fails.

## Affected Components

- sfb/tunnel/alice_tunnel.py
- sfb/reliability/send_window.py
- doc/ALICE_RETRANSMIT_LOGIC.md

## Design Notes

- Empty polls are always flagged KEEPALIVE; non-keepalive packets must carry at
  least one segment.
- Keepalive suppression on Bob (no keepalive when pending data) remains
  unchanged; this plan only changes Alice's poll behavior.
- Pending-data ACK tracking should follow unacked packets with segments, not
  keepalives.
- Send-window bookkeeping should only advance after a transport send succeeds.

## Implementation Steps

1. Update Alice's poll send path to derive the keepalive flag from `segments`
   (empty => KEEPALIVE) while preserving existing poll pacing, rate limiting,
   and window gating.
2. When Alice is about to send an empty poll and the send window is full, allow
   dropping the oldest keepalive even if the poll was triggered by grace or ACK
   progress, so the empty KEEPALIVE poll can go out.
3. Add a `data_unacked_count` or `has_data_unacked` helper on `SendWindow` and
   use it in `AliceTunnel.tick()` to clear `_has_pending_data_acks` once only
   keepalives remain unacked.
4. Reorder send accounting in `_send_new_packet` and `_send_retransmit` so the
   transport send happens before `send_window.send()` / `mark_retransmit()`;
   on send failure, log and exit without mutating send-window state.
5. Update `doc/ALICE_RETRANSMIT_LOGIC.md` to describe that empty polls always
   carry FLAG_KEEPALIVE, including during grace polls.

## Validation

- Run unit tests for tunnel/reliability behavior (do not run `tests/e2e/`).
