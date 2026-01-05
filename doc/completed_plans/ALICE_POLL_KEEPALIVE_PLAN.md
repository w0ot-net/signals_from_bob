# Alice Poll Keepalive Plan

Status: completed

## Goal

Ensure Alice never emits empty non-keepalive packets, make the polling rules
explicit and consistent with that requirement, keep keepalive-only unacked
packets from forcing fast polling, and avoid recording unacked sends when the
transport send fails.

## Affected Components

- sfb/tunnel/alice_tunnel.py
- sfb/reliability/send_window.py
- doc/ALICE_RETRANSMIT_LOGIC.md

## Current Behavior (Today)

### Empty poll keepalive flag

- Today: `_poll_decision()` returns `keepalive_due=False` for grace/ACK-progress
  polls, and the docs imply those are non-keepalive polls, but there is no
  explicit enforcement point that prevents empty non-keepalive packets from
  being emitted by future call sites.

### Empty poll when window is full

- Today: the code only drops the oldest keepalive when `keepalive_due=True`.
  If the send window is full of keepalive-only entries and a grace/ACK-progress
  poll triggers an empty poll, the poll is blocked even though only keepalives
  are in flight.

### Pending-data ACK tracking

- Today: `_has_pending_data_acks` is cleared only when `unacked_count == 0`.
  If real data is fully acked but keepalive-only packets remain unacked, Alice
  stays in fast-poll mode.

### Send-window accounting order

- Today: `send_window.send()` / `mark_retransmit()` runs before the transport
  send. If the transport send fails, we still have an unacked entry for a
  packet that never went out, which can clog the window and trigger retransmits.

## Planned Behavior (After Change)

### Empty poll keepalive flag

- After change: any packet with zero segments always carries FLAG_KEEPALIVE.
  We enforce this at the send path so empty non-keepalive packets cannot be
  emitted. Docs are updated to state that grace polls are still keepalive
  flagged. This makes the rule explicit and future-proof.

### Empty poll when window is full

- After change: when Alice is about to send an empty poll and the window is
  full, drop the oldest keepalive only if we are immediately sending a
  replacement keepalive poll. This never drops data; it replaces an old
  keepalive with a new keepalive so liveness probing continues when the window
  is full.

### Pending-data ACK tracking

- After change: `_has_pending_data_acks` tracks unacked packets with segments
  only. Keepalive-only unacked packets no longer keep Alice in fast-poll mode.

### Send-window accounting order

- After change: transport send happens before send-window bookkeeping. On send
  failure, we log and return without mutating send-window state.

## Implementation Steps

1. Enforce the rule "empty => FLAG_KEEPALIVE" in Alice's send path while
   preserving poll pacing, rate limiting, and window gating.
2. When an empty keepalive poll is about to be sent and the send window is
   full, drop the oldest keepalive only if the replacement keepalive poll will
   be sent immediately. If the send cannot proceed, do not drop anything.
   This never drops data segments; it only replaces a keepalive when the
   window is full.
3. Add a `data_unacked_count` or `has_data_unacked` helper on `SendWindow`, and
   use it in `AliceTunnel.tick()` to clear `_has_pending_data_acks` once no
   packets with segments remain unacked.
4. Reorder send accounting in `_send_new_packet` and `_send_retransmit` so the
   transport send happens before `send_window.send()` / `mark_retransmit()`.
   On send failure, log and exit without mutating send-window state.
5. Update `doc/ALICE_RETRANSMIT_LOGIC.md` to state that empty polls always
   carry FLAG_KEEPALIVE, including during grace polls.

## Validation

- Run unit tests for tunnel/reliability behavior (do not run `tests/e2e/`).

## Execution Notes

- Enforced keepalive flags on empty sends and updated keepalive replacement
  behavior when the send window is full.
- Added data-only unacked tracking to clear fast-poll state once data is acked.
- Reordered transport sends ahead of send-window accounting with send-failure
  logging.
- Updated retransmit/poll docs for keepalive flag and drop behavior.
- Tests not run (per instructions).
