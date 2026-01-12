# Base Tunnel Symmetry Expansion Plan

Status: draft

## Summary
Consolidate shared send/receive, logging, handshake, and close behaviors in
BaseTunnel while preserving the Alice-initiated, poll-driven asymmetry. Add a
small internal send-context API to let BaseTunnel own packet construction,
bookkeeping, and logging without coupling to transport details.

## Goals
- Centralize packet send/retransmit/keepalive mechanics in BaseTunnel.
- Standardize receive bookkeeping (bytes/last-recv) and common logging fields.
- Reduce duplicate handshake state transitions and close behavior.
- Keep protocol behavior and asymmetry unchanged.

## Non-Goals
- Change retransmit policy, pacing, keepalive rules, or MTU/window semantics.
- Add or run automated tests.
- Modify transport protocol semantics beyond new internal hooks.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Define a minimal internal send-context API in `sfb/tunnel/base_tunnel.py`.
   - Add a small `SendContext` data holder (send callable, payload_cap,
     release callable, log metadata) to unify Alice transport sends and Bob
     responder sends.
   - Keep the API internal and require explicit adapters in Alice/Bob.

2. Move packet send and retransmit logic into BaseTunnel.
   - Add BaseTunnel helpers for:
     - building flags + packet + encryption,
     - sending new packets,
     - retransmitting with existing encrypted bodies,
     - common stats/logging updates.
   - Update Alice/Bob to pass a `SendContext` and handle only transport-specific
     permit/responder acquisition.
   - Make this a clean break by removing the old per-side send helpers and
     updating all call sites in the same change.

3. Centralize common send-window blocked logging.
   - Add BaseTunnel helpers for `window_full` and `window_distance` logging with
     consistent fields.
   - Keep Alice-only pacer/rate-limit logs in Alice, but use Base helpers for
     the shared cases.

4. Add a shared receive path for decode + bookkeeping.
   - Introduce a BaseTunnel `_decode_and_process` helper that:
     - decodes packet bytes,
     - increments `_bytes_received`,
     - optionally updates last-recv timestamps,
     - invokes `_process_incoming_packet`.
   - Use it for Bob request handling and Alice response handling to remove
     duplication.

5. Unify handshake state transitions and connected logging.
   - Add Base helpers to:
     - initialize ISN/recv window state from a remote SYN,
     - log connected transitions with shared fields.
   - Keep Alice handshake loop and Bob SYN/SYN+ACK response logic intact.

6. Move transport shutdown into a Base hook.
   - Add BaseTunnel `_close_transport` as a no-op by default and call it from
     `BaseTunnel.close`.
   - Replace subclass `close` overrides with `_close_transport` overrides.

7. Document the new symmetry boundary.
   - Update `doc/architecture/ASYMMETRY.md` to describe which behaviors are
     shared in BaseTunnel and which remain Alice/Bob specific.

## Testing
- Do not run tests.
