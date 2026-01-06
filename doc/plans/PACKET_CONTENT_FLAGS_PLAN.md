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
- tests/test_tunnel.py

## Design Notes

- Add two content flags (use reserved bits 3-7):
  - `FLAG_HAS_SEGMENTS` (bit 3 / 0x08): packet contains one or more segments.
  - `FLAG_POLL` (bit 4 / 0x10): packet contains zero segments and indicates
    "poll again soon" (pending data or suppressed keepalive).
- Keep `FLAG_KEEPALIVE` as the explicit idle keepalive indicator.
- Content flag rules (CONNECTED state):
  - Exactly one of `{HAS_SEGMENTS, POLL, KEEPALIVE}` must be set.
  - `HAS_SEGMENTS` requires at least one segment.
  - `POLL` and `KEEPALIVE` require zero segments.
- Handshake rules:
  - SYN/SYN+ACK/ACK packets must have zero segments and no content flags set.
- Replace "ack-only" terminology in docs/logs with "poll hint" (`POLL`) to make
  the intent explicit.
- Other possible flags considered (RESET/FIN, CONTROL_ONLY) are deferred to
  separate work to keep this change focused on empty-packet clarity.

## Implementation Steps

1. Define new flags in `sfb/protocol/constants.py` and update
   `PacketHeader._VALID_FLAGS` in `sfb/protocol/packet.py`.
2. Extend `PacketHeader`/`Packet` helpers and repr output to surface the new
   flags in logs and debugging.
3. Replace `_validate_keepalive_packet()` in `sfb/tunnel/base_tunnel.py` with
   content-flag validation that enforces the rules above (including handshake
   constraints).
4. Update send paths:
   - Alice: set `HAS_SEGMENTS` when sending segments, `KEEPALIVE` on idle polls,
     and no content flags during handshake.
   - Bob: set `HAS_SEGMENTS` when sending segments, `POLL` when responding with
     empty packets due to pending data, and `KEEPALIVE` when idle.
5. Update receive paths:
   - Alice: treat `POLL` as a "not idle" response (immediate poll behavior),
     and treat `KEEPALIVE` as idle.
   - Bob: continue to ignore keepalive segments as today, but validate content
     flags for protocol correctness.
6. Update documentation to describe the new flags, the content-flag rules, and
   the explicit "poll hint" semantics.
7. Add unit tests that validate:
   - content-flag/segment mismatch is a protocol violation,
   - handshake packets reject content flags,
   - Alice polling behavior distinguishes `POLL` vs `KEEPALIVE`,
   - Bob emits `POLL` when pending data exists but no segments fit.

## Validation

- Run unit tests for packet validation and tunnel polling behavior.
- Do not run tests in `tests/e2e/`.
