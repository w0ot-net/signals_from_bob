# Base Tunnel Symmetry Expansion Phase 2 Plan

Status: draft

## Summary
Phase 2 of `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`. Consolidate
receive decoding/processing and handshake state transitions in BaseTunnel while
preserving Alice-driven polling and Bob's request/response behavior.

## Goals
- Share a single decode-and-process path that updates bytes/last-recv and
  invokes `_process_incoming_packet`.
- Unify handshake state initialization and connected logging between Alice and
  Bob.
- Keep all asymmetric behaviors and timeout policies unchanged.

## Non-Goals
- Change send/retransmit behavior (Phase 1 scope).
- Change transport polling, pacing, or timeout rules.
- Update architecture docs (Phase 3 scope).
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`

## Constraints
- Python 2.7 + 3 compatible and ASCII-only code in `sfb/`.
- Avoid list/dict/set comprehensions and generator expressions in `sfb/`.
- Preserve asymmetry: Alice initiates/polls, Bob replies opportunistically.
- Maintain existing timeout semantics (Alice RTT-based, Bob wall-clock).

## Plan
1. Add a BaseTunnel `_decode_and_process` helper.
   - Inputs: `data`, `now`, optional `packet_size`, and a flag or callback to
     update last-recv timestamps (Alice uses `_last_recv_time`).
   - Use `_decode_packet(..., return_size=True)`; return `(None, None, None)`
     on decode failure without side effects beyond logging.
   - Update `_bytes_received` based on raw `data` length.
   - Call `_process_incoming_packet(packet, now=now, packet_size=packet_size)`
     and return `(packet, packet_size, (rtt_samples, acked_count,
     data_acked_count))` so callers can apply side-specific logic.

2. Apply the helper in Bob request handling.
   - In `handle_request`, replace direct `_decode_packet` and byte accounting
     with `_decode_and_process`.
   - Pass the decoded packet into `_handle_handshake` / `_handle_data` and
     remove duplicated `_bytes_received` increments.
   - Keep `_update_poll_ewma` and idle timeout checks unchanged.

3. Apply the helper in Alice response handling.
   - Replace `_handle_response` decode + bytes + `_last_recv_time` updates
     with `_decode_and_process`, using the new last-recv hook.
   - Preserve handshake-late packet filtering, response_kind detection, and
     pacer/RTT updates that depend on `rtt_samples` and `data_acked_count`.

4. Add BaseTunnel handshake initialization helpers.
   - Add a helper that sets `_local_isn`, `_remote_isn`, initializes
     `_send_window._next_seq`, and calls `_recv_window.set_initial_seq` with
     `(remote_isn + 1) & 0xFFFF`.
   - Use the helper in Bob `_handle_handshake` when processing SYN packets.
   - Use the helper in Alice `connect` / `_complete_handshake` when a valid
     SYN+ACK is received.

5. Centralize connected logging.
   - Add a BaseTunnel `_log_connected(mode)` that emits `tunnel.connected`
     with consistent fields (`local_isn`, `remote_isn`, `mode`, `side`).
   - Replace direct `log_event` calls in Alice and Bob with this helper.

6. Preserve state transitions and invariants.
   - Keep CONNECTING -> HANDSHAKE_ACKED -> CONNECTED transitions as-is.
   - Do not alter validation rules for handshake flags or late packets.
   - Ensure `_process_incoming_packet` remains the sole owner of ACK/SACK
     updates and recv_window delivery.

## References
- `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`

## Testing
- Do not run tests.
