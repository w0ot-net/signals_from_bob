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
  - `POLL`: `_got_data = False`, `_last_was_pong_only = False` (not idle, not data).
  - `KEEPALIVE`: `_got_data = False`, `_last_was_pong_only = True`.
- Content flag rules (non-handshake packets, any state):
  - Exactly one of `{HAS_SEGMENTS, POLL, KEEPALIVE}` must be set.
  - `HAS_SEGMENTS` requires at least one segment.
  - `POLL` and `KEEPALIVE` require zero segments.
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
- Other possible flags considered (RESET/FIN, CONTROL_ONLY) are deferred to
  separate work to keep this change focused on empty-packet clarity.

## Implementation Steps

1. Define new flags in `sfb/protocol/constants.py`, update
   `PacketHeader._VALID_FLAGS` in `sfb/protocol/packet.py`, and re-export the
   new flags in `sfb/protocol/__init__.py` (`imports` + `__all__`).
2. Extend `PacketHeader`/`Packet` helpers and repr output to surface the new
   flags in logs and debugging.
3. Replace `_validate_keepalive_packet()` in `sfb/tunnel/base_tunnel.py` with
   content-flag validation that enforces the rules above (including CONNECTING
   rejection of non-handshake packets).
4. Update send paths:
   - Alice: set `HAS_SEGMENTS` when sending segments, `KEEPALIVE` on idle polls,
     and no content flags during handshake.
   - Bob: set `HAS_SEGMENTS` when sending segments, `POLL` when responding with
     empty packets due to pending data, and `KEEPALIVE` when idle.
5. Enforce strict handshake completion:
   - Bob: remove implicit-ACK handling for non-handshake packets while
     CONNECTING.
   - Alice: if final ACK exchange fails, revert to CONNECTING and retry
     (do not mark CONNECTED or start negotiation on failure).
6. Update receive paths:
   - Alice: treat `POLL` as a "not idle" response (immediate poll behavior),
     treat `KEEPALIVE` as idle, and map `_got_data`/`_last_was_pong_only`
     explicitly for `HAS_SEGMENTS`/`POLL`/`KEEPALIVE`.
   - Bob: continue to ignore keepalive segments as today, but validate content
     flags for protocol correctness.
7. Update documentation to describe the new flags, the content-flag rules, and
   the explicit "poll hint" semantics, including a sweep to replace ack-only
   wording in `doc/ARCHITECTURE.md` and `doc/DNS_TRANSPORT.md`.
8. Add unit tests that validate:
   - content-flag/segment mismatch is a protocol violation,
   - handshake packets reject content flags,
   - Alice polling behavior distinguishes `POLL` vs `KEEPALIVE`,
   - Bob emits `POLL` when pending data exists but no segments fit.

## Validation

- Run unit tests for packet validation and tunnel polling behavior.
- Do not run tests in `tests/e2e/`.
