# Reliability Refactor Plan: Send-Window Distance and Pacer

Status: draft

## Goal

- Move send-window distance helpers and cumulative ACK progress tracking from
  tunnel code into the reliability layer.
- Move the pacer implementation from `sfb/tunnel/pacing.py` into
  `sfb/reliability/` without changing behavior.

## Affected Components

- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/pacing.py (move/rename)
- sfb/reliability/send_window.py
- sfb/reliability/__init__.py
- doc/ALICE_RETRANSMIT_LOGIC.md
- doc/RELIABILITY.md
- doc/TUNNEL.md
- doc/ARCHITECTURE.md (if it references pacer or send-window helpers)

## Design Notes

- SendWindow owns cumulative ACK state:
  - Add `last_cum_ack`, `last_cum_ack_time`, and `last_ack_progress_time` to
    `SendWindow`.
  - Provide `ack_silence(now)` and `ack_progress_silence(now)` that return
    raw float seconds or None; round only in logging.
  - Expose read-only accessors for the ACK tracking fields to avoid tunnel
    code reaching into SendWindow internals.
- Move ACK/SACK progress tracking into reliability:
  - Extend `SendWindow` with a method that updates cumulative ACK state,
    SACK progress state, and then processes ACK/SACK for window cleanup.
  - Return the same values BaseTunnel uses today plus previous cumulative ACK
    fields and whether ACK progress occurred (unacked count decreased), so
    logging and window-growth logic remain intact.
  - Keep sequence wrap handling with `seq_gt` from the protocol layer.
  - Update `sack_progress_ready()` to use SendWindow-owned cumulative ACK
    state (no external `cum_ack` parameter).
- Move send-window distance helpers into `SendWindow`:
  - Implement `distance_info(cap_override=None, max_window=None)` and
    `distance_exceeded(...)` using `last_cum_ack`, `next_seq`, and
    `unacked_count`.
  - Implement `distance_details(now)` using existing send-window debug info,
    ACK history, and keepalive drop info.
- BaseTunnel becomes a consumer of `SendWindow` reliability state and should
  drop `_last_cum_ack`, `_last_cum_ack_time`, and `_last_ack_progress_time`.
- The pacer remains owned by Alice but is imported from
  `sfb/reliability/pacing.py` (same class and behavior, new location).

## Implementation Steps

1. Extend `SendWindow` with cumulative ACK state, read-only accessors, and
   silence helper methods that return raw float seconds.
2. Add a `SendWindow` method to update ACK/SACK progress and process ACKs
   in one place, returning BaseTunnel's current values plus previous ACK
   fields and an `ack_progressed` flag for window growth gating.
3. Move send-window distance helpers from BaseTunnel into `SendWindow`.
4. Refactor BaseTunnel to use `SendWindow` ACK update results for logging and
   silence calculations; remove tunnel-level ACK tracking fields.
5. Update Alice and Bob tunnel code to use `SendWindow` accessors for:
   - ACK silence gating (raw values for logic, rounded in logs)
   - SACK progress readiness (no external cum_ack parameter)
   - Bob retransmit cooldown logging fields
   - Alice window-growth gating and fast retransmit checks
6. Move `sfb/tunnel/pacing.py` to `sfb/reliability/pacing.py`, update imports,
   and export the class in `sfb/reliability/__init__.py`.
7. Update docs to reflect new ownership of ACK tracking and pacer placement
   (including `doc/ALICE_RETRANSMIT_LOGIC.md`).

## Validation

- Run the existing unit tests that cover reliability and tunnel behavior.
- Do not run `tests/e2e/`.
