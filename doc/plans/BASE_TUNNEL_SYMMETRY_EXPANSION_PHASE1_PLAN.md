# Base Tunnel Symmetry Expansion Phase 1 Plan

Status: draft

## Summary
Phase 1 of `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`. Consolidate
packet send and retransmit mechanics into BaseTunnel using a minimal
transport-agnostic send context, while preserving Alice/Bob asymmetry and
gating rules.

## Goals
- Centralize packet construction, encryption, send_window bookkeeping, and
  packet_send logging in BaseTunnel.
- Add a small SendContext API that isolates transport-specific send behavior
  from shared packet logic.
- Unify window_full and window_distance logging fields between Alice and Bob.
- Make a clean break by removing per-side send helpers and updating all call
  sites in the same change.

## Non-Goals
- Change retransmit policy, pacing, or keepalive suppression behavior.
- Change MTU/window negotiation semantics or packet formats.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`

## Constraints
- Python 2.7 + 3 compatible and ASCII-only code in `sfb/`.
- Avoid list/dict/set comprehensions and generator expressions in `sfb/`.
- Preserve asymmetry: Alice initiates/polls, Bob replies opportunistically.
- Keep keepalive suppression when any channel has pending data.

## Plan
1. Define an internal SendContext in `sfb/tunnel/base_tunnel.py`.
   - Use a small class with explicit attributes (no namedtuple) for:
     - `send_fn(packet_data)` callable for transport send.
     - `payload_cap` or `packet_cap` to enforce response caps.
     - `release_fn()` callable for permit/responder cleanup (optional).
     - `log_context` string to label the send path in log fields.
   - Add a light validator/helper to ensure required fields are present and
     to normalize absent `release_fn` to a no-op.

2. Add shared packet send helpers in BaseTunnel.
   - Introduce a helper that:
     - Normalizes flags (sets KEEPALIVE vs HAS_SEGMENTS from segments).
     - Builds packets with `_build_packet` or `_rebuild_packet`.
     - Encodes/encrypts via `_encode_packet_for_send`, reusing encrypted
       bodies for retransmits.
     - Invokes `send_fn` and handles errors consistently.
     - Updates `_packets_sent`, `_bytes_sent`, and send_window state
       (`send` for new, `mark_retransmit` for retransmits).
     - Emits `tunnel.packet_send` with `_packet_send_fields`.
   - Keep return values explicit (success flag + packet data lengths) to
     preserve existing caller behavior.

3. Add a BaseTunnel retransmit helper.
   - Capture and log prior send age, retransmit counts, and optional reason.
   - Emit `tunnel.retransmit` and `tunnel.reliability_state` with the same
     fields Alice/Bob currently supply.
   - Preserve Alice RTT/backoff and Bob cooldown policies by keeping gating
     outside the Base helper.

4. Integrate SendContext in Alice send paths.
   - Add a small adapter in `sfb/tunnel/alice_tunnel.py` that:
     - Builds SendContext from a transport permit.
     - Uses `payload_cap_for_send` for per-send caps.
     - Releases permits on failure using `release_fn`.
   - Update `_send_new_packet` and `_send_retransmit` to delegate to Base
     helpers after existing gating (rate limit, pacing, permit reservation).
   - Update `_try_send_segments` and `_send_keepalive_or_break` to call the
     shared send helper without changing poll/keepalive decisions.

5. Integrate SendContext in Bob response paths.
   - Add a responder adapter in `sfb/tunnel/bob_tunnel.py` using:
     - `responder(response_data)` as `send_fn`.
     - `response_payload_cap` for payload limits.
     - A no-op `release_fn`.
   - Replace `_send_response_packet`, `_send_retransmit_response`,
     `_send_keepalive_response`, and `_send_segments_response` with Base
     helper usage.
   - Preserve `_log_response_cap` ordering and fields.

6. Centralize window_full and window_distance logging in BaseTunnel.
   - Add Base helpers that emit the common `tunnel.send_window_full`,
     `tunnel.send_window_distance`, `tunnel.send_blocked`, and
     `tunnel.reliability_state` events with shared fields.
   - Update Alice/Bob `_log_send_blocked` to delegate for shared cases while
     keeping Alice-only pacer/rate-limit logs local.

7. Clean up duplicate send logic and call sites.
   - Remove or simplify per-side send helpers that are fully replaced.
   - Update all call sites in the same change to avoid compatibility shims.

## References
- `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`

## Testing
- Do not run tests.
