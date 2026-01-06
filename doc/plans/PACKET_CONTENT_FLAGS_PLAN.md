# Packet Content Flags Plan

Status: draft

## Goal

Make packet intent unambiguous on the wire by explicitly distinguishing:
- packets with segments,
- empty packets that mean "idle keepalive",
- empty packets that mean "poll again soon (pending data)".

This removes the current ambiguity where empty packets with no KEEPALIVE flag
serve as an implicit poll hint.

## Affected Components

- sfb/protocol/constants.py
- sfb/protocol/packet.py
- sfb/protocol/__init__.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/reliability/send_window.py
- doc/PROTOCOL.md
- doc/TUNNEL.md
- doc/ASYMMETRY.md
- doc/RELIABILITY.md
- doc/ALICE_RETRANSMIT_LOGIC.md
- doc/BOB_RETRANSMIT_LOGIC.md
- doc/ARCHITECTURE.md
- doc/DNS_TRANSPORT.md
- tests/test_tunnel.py

## Design Notes

- Add two content flags (use reserved bits 3-7):
  - `FLAG_HAS_SEGMENTS` (bit 3 / 0x08): packet contains one or more segments.
  - `FLAG_WANTS_POLL` (bit 4 / 0x10): packet contains zero segments and indicates
    "poll again soon" (pending data or suppressed keepalive).
- Keep `FLAG_KEEPALIVE` as the explicit idle keepalive indicator.
- Alice response classification must be explicit:
  - `HAS_SEGMENTS`: `_got_data = True`, `_last_was_pong_only = False`.
  - `WANTS_POLL`: `_got_data = False`, `_last_was_pong_only = False`,
    and `WANTS_POLL` must still trigger an immediate poll via a dedicated hint
    (do not rely on `_got_data`).
  - `KEEPALIVE`: `_got_data = False`, `_last_was_pong_only = True`.
- Content flag rules (non-handshake packets after handshake):
  - Exactly one of `{HAS_SEGMENTS, WANTS_POLL, KEEPALIVE}` must be set.
  - `HAS_SEGMENTS` requires at least one segment.
  - `WANTS_POLL` and `KEEPALIVE` require zero segments.
  - Apply only in post-ACK states (HANDSHAKE_ACKED/CONNECTED); CONNECTING accepts
    only SYN/SYN+ACK/ACK and rejects non-handshake packets before content flags
    are validated.
- RTT sampling treats `WANTS_POLL` like `KEEPALIVE` (no RTT samples or backoff
  reset for WANTS_POLL-only packets). Gate RTT sampling off the response flags
  by passing a `sample_rtt` hint into send-window ACK processing.
- Keep `drop_keepalive`/`drop_oldest_keepalive` scoped to `FLAG_KEEPALIVE` only;
  `WANTS_POLL` is a non-idle hint and should not be eligible for keepalive
  drops. Track it separately in send-window debug state.
- Handshake rules:
  - SYN/SYN+ACK/ACK packets must have zero segments and no content flags set.
  - While CONNECTING, accept only SYN/SYN+ACK/ACK and reject any other packets
    (no implicit-ACK data without content flags).
  - Add a post-ACK state for Alice (e.g., `HANDSHAKE_ACKED`):
    - Enter after sending the final ACK and before the first post-ACK response.
    - Accept non-handshake packets with valid content flags in this state, but
      still reject SYN/ACK flags.
    - Transition to CONNECTED on the first valid post-ACK response.
    - On timeout/failure, set DISCONNECTED and raise a handshake timeout so
      connect() restarts from scratch with a new ISN (no in-place retry).
  - Late SYN/SYN+ACK after handshake (HANDSHAKE_ACKED or CONNECTED) should be
    treated as stale/duplicate and ignored (or answered with a normal ACK),
    not as a protocol violation that closes the tunnel.
  - Content-flag validation must allow handshake packets to pass through so
    late SYN/SYN+ACK handling can apply without triggering a violation.
- Replace "ack-only" terminology in docs/logs with "poll hint" (`WANTS_POLL`) to make
  the intent explicit.
- Update log context strings and the protocol module example to emit
  `HAS_SEGMENTS`/`WANTS_POLL`/`KEEPALIVE` explicitly instead of "ack_only".
- Update tunnel packet logging to include explicit content-flag intent (e.g.,
  `content_flag` or `poll` fields) so WANTS_POLL vs KEEPALIVE is visible without
  decoding numeric flags.
- Update `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md` to remove the old
  keepalive-only/ack-only split and describe `WANTS_POLL`/`KEEPALIVE` explicitly.
- Other possible flags considered (RESET/FIN, CONTROL_ONLY) are deferred to
  separate work to keep this change focused on empty-packet clarity.

## Implementation Steps

1. Define new flags in `sfb/protocol/constants.py`, update
   `PacketHeader._VALID_FLAGS` in `sfb/protocol/packet.py`, and re-export the
   new flags in `sfb/protocol/__init__.py` (`imports` + `__all__`).
2. Extend `PacketHeader`/`Packet` helpers and repr output to surface the new
   flags in logs and debugging.
3. Update `sfb/reliability/send_window.py` RTT sampling to accept a `sample_rtt`
   hint (from the receive path) and skip samples when `sample_rtt` is false or
   `FLAG_KEEPALIVE` is set. Pass `sample_rtt` from
   `sfb/tunnel/base_tunnel.py` based on `FLAG_WANTS_POLL` in the response.
4. Update send-window debug accounting:
   - Keep `drop_keepalive`/`drop_oldest_keepalive` limited to `FLAG_KEEPALIVE`.
   - Add `wants_poll_unacked` (and `oldest_wants_poll`) in `debug_state()` and
     `distance_details()` so `WANTS_POLL` is not lumped into `empty_unacked`.
5. Replace `_validate_keepalive_packet()` in `sfb/tunnel/base_tunnel.py` with
   state-aware content-flag validation: allow handshake packets to bypass
   content-flag checks; reject non-handshake packets while CONNECTING; enforce
   the content-flag rules in HANDSHAKE_ACKED/CONNECTED without treating late
   SYN/SYN+ACK as a protocol violation.
6. Update send paths:
   - Alice: set `HAS_SEGMENTS` when sending segments, `KEEPALIVE` on idle polls,
     and no content flags during handshake.
   - Bob: set `HAS_SEGMENTS` when sending segments, `WANTS_POLL` when responding with
     empty packets due to pending data, and `KEEPALIVE` when idle.
   - Do not OR `FLAG_KEEPALIVE` onto pre-set content flags for empty packets.
     Choose exactly one content flag and pass `packet.flags` (post-build) into
     `SendWindow.send()` so retransmits preserve `WANTS_POLL` vs `KEEPALIVE`.
7. Add the post-ACK state for Alice (e.g., `HANDSHAKE_ACKED`) and update the
   handshake flow/validation to use it (enter after final ACK, accept content
   flags, reject SYN/ACK, transition to CONNECTED on first response, set
   DISCONNECTED + raise handshake timeout on failure so connect() retries
   from scratch).
8. Update handshake validation to ignore (or ACK) late SYN/SYN+ACK packets
   after handshake completion rather than treating them as protocol violations.
9. Enforce strict handshake completion:
   - Bob: remove implicit-ACK handling for non-handshake packets while
     CONNECTING.
10. Update receive paths:
   - Alice: add explicit `WANTS_POLL` handling in `_handle_response()` and
     `_poll_decision()` (or a new poll-hint state) so `WANTS_POLL` triggers immediate
     polling without marking `_got_data` or `KEEPALIVE`.
   - Alice: treat `KEEPALIVE` as idle, and map `_got_data`/`_last_was_pong_only`
     explicitly for `HAS_SEGMENTS`/`WANTS_POLL`/`KEEPALIVE`.
   - Bob: continue to ignore keepalive segments as today, but validate content
     flags for protocol correctness.
11. Update documentation to describe the new flags, the content-flag rules, and
  the explicit "poll hint" semantics, including a sweep to replace ack-only
  wording in `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md`.
12. Add unit tests that validate:
   - content-flag/segment mismatch is a protocol violation,
   - handshake packets reject content flags,
   - Alice polling behavior distinguishes `WANTS_POLL` vs `KEEPALIVE`,
   - Bob emits `WANTS_POLL` when pending data exists but no segments fit,
   - `WANTS_POLL` does not generate RTT samples.
   - The post-ACK state accepts content-flag packets and transitions to
     CONNECTED, while rejecting SYN/ACK.

## Validation

- Run unit tests for packet validation and tunnel polling behavior.
- Do not run tests in `tests/e2e/`.
