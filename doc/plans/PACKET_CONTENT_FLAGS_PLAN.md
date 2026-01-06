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
  - `FLAG_POLL` (bit 4 / 0x10): packet contains zero segments and indicates
    "poll again soon" (pending data or suppressed keepalive).
- Keep `FLAG_KEEPALIVE` as the explicit idle keepalive indicator.
- Alice response classification must be explicit:
  - `HAS_SEGMENTS`: `_got_data = True`, `_last_was_pong_only = False`.
  - `POLL`: `_got_data = False`, `_last_was_pong_only = False`,
    and `POLL` must still trigger an immediate poll via a dedicated hint
    (do not rely on `_got_data`).
  - `KEEPALIVE`: `_got_data = False`, `_last_was_pong_only = True`.
- Content flag rules (non-handshake packets, any state):
  - Exactly one of `{HAS_SEGMENTS, POLL, KEEPALIVE}` must be set.
  - `HAS_SEGMENTS` requires at least one segment.
  - `POLL` and `KEEPALIVE` require zero segments.
- RTT sampling treats `POLL` like `KEEPALIVE` (no RTT samples or backoff reset
  for POLL-only packets).
- Handshake rules:
  - SYN/SYN+ACK/ACK packets must have zero segments and no content flags set.
  - While CONNECTING, accept only SYN/SYN+ACK/ACK and reject any other packets
    (no implicit-ACK data without content flags).
  - Alice remains CONNECTING unless the final ACK exchange succeeds; do not
    treat ACK send failures as connected.
- Replace "ack-only" terminology in docs/logs with "poll hint" (`POLL`) to make
  the intent explicit.
- Update log context strings and the protocol module example to emit
  `HAS_SEGMENTS`/`POLL`/`KEEPALIVE` explicitly instead of "ack_only".
- Update `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md` to remove the old
  keepalive-only/ack-only split and describe `POLL`/`KEEPALIVE` explicitly.
- Other possible flags considered (RESET/FIN, CONTROL_ONLY) are deferred to
  separate work to keep this change focused on empty-packet clarity.

## Implementation Steps

1. Define new flags in `sfb/protocol/constants.py`, update
   `PacketHeader._VALID_FLAGS` in `sfb/protocol/packet.py`, and re-export the
   new flags in `sfb/protocol/__init__.py` (`imports` + `__all__`).
2. Extend `PacketHeader`/`Packet` helpers and repr output to surface the new
   flags in logs and debugging.
3. Update `sfb/reliability/send_window.py` RTT sampling to skip `FLAG_POLL`
   (same policy as `FLAG_KEEPALIVE`) and adjust imports.
4. Replace `_validate_keepalive_packet()` in `sfb/tunnel/base_tunnel.py` with
   content-flag validation that enforces the rules above (including CONNECTING
   rejection of non-handshake packets).
5. Update send paths:
   - Alice: set `HAS_SEGMENTS` when sending segments, `KEEPALIVE` on idle polls,
     and no content flags during handshake.
   - Bob: set `HAS_SEGMENTS` when sending segments, `POLL` when responding with
     empty packets due to pending data, and `KEEPALIVE` when idle.
   - Do not OR `FLAG_KEEPALIVE` onto pre-set content flags for empty packets.
     Choose exactly one content flag and pass `packet.flags` (post-build) into
     `SendWindow.send()` so retransmits preserve `POLL` vs `KEEPALIVE`.
6. Enforce strict handshake completion:
   - Bob: remove implicit-ACK handling for non-handshake packets while
     CONNECTING.
   - Alice: if final ACK exchange fails, revert to CONNECTING and retry
     (do not mark CONNECTED or start negotiation on failure).
7. Update receive paths:
   - Alice: add explicit `POLL` handling in `_handle_response()` and
     `_poll_decision()` (or a new poll-hint state) so `POLL` triggers immediate
     polling without marking `_got_data` or `KEEPALIVE`.
   - Alice: treat `KEEPALIVE` as idle, and map `_got_data`/`_last_was_pong_only`
     explicitly for `HAS_SEGMENTS`/`POLL`/`KEEPALIVE`.
   - Bob: continue to ignore keepalive segments as today, but validate content
     flags for protocol correctness.
8. Update documentation to describe the new flags, the content-flag rules, and
   the explicit "poll hint" semantics, including a sweep to replace ack-only
   wording in `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md`.
9. Add unit tests that validate:
   - content-flag/segment mismatch is a protocol violation,
   - handshake packets reject content flags,
   - Alice polling behavior distinguishes `POLL` vs `KEEPALIVE`,
   - Bob emits `POLL` when pending data exists but no segments fit,
   - `POLL` does not generate RTT samples.

## Validation

- Run unit tests for packet validation and tunnel polling behavior.
- Do not run tests in `tests/e2e/`.
