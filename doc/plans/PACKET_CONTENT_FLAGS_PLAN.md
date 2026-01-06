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
- Content flag rules (non-handshake packets, any state):
  - Exactly one of `{HAS_SEGMENTS, WANTS_POLL, KEEPALIVE}` must be set.
  - `HAS_SEGMENTS` requires at least one segment.
  - `WANTS_POLL` and `KEEPALIVE` require zero segments.
- RTT sampling treats `WANTS_POLL` like `KEEPALIVE` (no RTT samples or backoff
  reset for WANTS_POLL-only packets).
- Handshake rules:
  - SYN/SYN+ACK/ACK packets must have zero segments and no content flags set.
  - While CONNECTING, accept only SYN/SYN+ACK/ACK and reject any other packets
    (no implicit-ACK data without content flags).
  - Add a post-ACK state for Alice (e.g., `HANDSHAKE_ACKED`):
    - Enter after sending the final ACK and before the first post-ACK response.
    - Accept non-handshake packets with valid content flags in this state, but
      still reject SYN/ACK flags.
    - Transition to CONNECTED on the first valid post-ACK response.
    - Revert to CONNECTING on timeout/failure; do not start negotiation.
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
3. Update `sfb/reliability/send_window.py` RTT sampling to skip `FLAG_WANTS_POLL`
   (same policy as `FLAG_KEEPALIVE`) and adjust imports.
4. Replace `_validate_keepalive_packet()` in `sfb/tunnel/base_tunnel.py` with
   content-flag validation that enforces the rules above (including CONNECTING
   rejection of non-handshake packets).
5. Update send paths:
   - Alice: set `HAS_SEGMENTS` when sending segments, `KEEPALIVE` on idle polls,
     and no content flags during handshake.
   - Bob: set `HAS_SEGMENTS` when sending segments, `WANTS_POLL` when responding with
     empty packets due to pending data, and `KEEPALIVE` when idle.
   - Do not OR `FLAG_KEEPALIVE` onto pre-set content flags for empty packets.
     Choose exactly one content flag and pass `packet.flags` (post-build) into
     `SendWindow.send()` so retransmits preserve `WANTS_POLL` vs `KEEPALIVE`.
6. Add the post-ACK state for Alice (e.g., `HANDSHAKE_ACKED`) and update the
   handshake flow/validation to use it (enter after final ACK, accept content
   flags, reject SYN/ACK, transition to CONNECTED on first response, revert to
   CONNECTING on failure).
7. Enforce strict handshake completion:
   - Bob: remove implicit-ACK handling for non-handshake packets while
     CONNECTING.
8. Update receive paths:
   - Alice: add explicit `WANTS_POLL` handling in `_handle_response()` and
     `_poll_decision()` (or a new poll-hint state) so `WANTS_POLL` triggers immediate
     polling without marking `_got_data` or `KEEPALIVE`.
   - Alice: treat `KEEPALIVE` as idle, and map `_got_data`/`_last_was_pong_only`
     explicitly for `HAS_SEGMENTS`/`WANTS_POLL`/`KEEPALIVE`.
   - Bob: continue to ignore keepalive segments as today, but validate content
     flags for protocol correctness.
9. Update documentation to describe the new flags, the content-flag rules, and
  the explicit "poll hint" semantics, including a sweep to replace ack-only
  wording in `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md`.
10. Add unit tests that validate:
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
