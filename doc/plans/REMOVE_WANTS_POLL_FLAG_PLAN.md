# Remove WANTS_POLL Flag Plan

## Goal
- Eliminate the `FLAG_WANTS_POLL` content flag and poll-hint behavior now that
  every Bob response can always fit at least one segment.
- Keep request/response behavior, pacing, and keepalive logic unchanged aside
  from removing the poll-hint path.
- Update protocol and transport docs to reflect the two remaining content flags
  (`HAS_SEGMENTS`, `KEEPALIVE`).

## Non-Goals
- Change retransmit, windowing, or pacing rules.
- Modify transport MTU/response-cap enforcement logic (assumed already in
  place).
- Update or run tests.

## Affected Components
- sfb/protocol/constants.py
- sfb/protocol/packet.py
- sfb/protocol/__init__.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/reliability/send_window.py
- doc/PROTOCOL.md
- doc/TUNNEL.md
- doc/RELIABILITY.md
- doc/ARCHITECTURE.md
- doc/DNS_TRANSPORT.md
- doc/ALICE_RETRANSMIT_LOGIC.md
- doc/BOB_RETRANSMIT_LOGIC.md
- doc/ASYMMETRY.md

## Plan
1) Remove the protocol flag:
   - Delete `FLAG_WANTS_POLL` from constants and exports.
   - Update PacketHeader validation/formatting to only allow
     `KEEPALIVE|HAS_SEGMENTS` content flags.
2) Update BaseTunnel content flag handling:
   - Remove `wants_poll` fields from `_packet_send_fields`.
   - Simplify `_content_flag_label` and `_validate_content_flags` to only accept
     `HAS_SEGMENTS` or `KEEPALIVE` on non-handshake packets.
3) Remove poll-hint behavior in Alice:
   - Drop `_poll_hint` state and any handling of `wants_poll` response kinds.
   - Simplify `_poll_decision` to use only `_got_data` and
     `_has_pending_data_acks` for immediate polling.
4) Remove poll-hint behavior in Bob:
   - Delete `_send_poll_hint_response` and the `poll_hint` action path.
   - In `_send_response`, collect segments without `return_pending`; if no
     segments, fall back to a keepalive response.
5) Update reliability debug fields:
   - Remove `wants_poll_unacked` and `missing/oldest_wants_poll` fields from
     send-window state/distance details.
6) Update docs to remove `WANTS_POLL` references and describe the new invariant
   that responses always fit at least one segment, including any revised
   keepalive behavior descriptions.
