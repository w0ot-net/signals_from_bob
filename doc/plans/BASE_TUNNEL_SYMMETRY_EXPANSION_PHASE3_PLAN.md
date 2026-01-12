# Base Tunnel Symmetry Expansion Phase 3 Plan

Status: draft

## Summary
Phase 3 of `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`. Finalize the
shared shutdown boundary and document the updated symmetry line between Alice
and Bob.

## Goals
- Move transport shutdown into a BaseTunnel hook and remove subclass `close`
  overrides.
- Document the finalized BaseTunnel shared behaviors vs Alice/Bob specifics.
- Ensure the change is a clean break with no leftover compatibility shims.

## Non-Goals
- Change send/receive/handshake logic (Phases 1 and 2 scope).
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/ASYMMETRY.md`

## Constraints
- Python 2.7 + 3 compatible and ASCII-only code in `sfb/`.
- Avoid list/dict/set comprehensions and generator expressions in `sfb/`.
- Preserve Alice/Bob asymmetry and existing timeout semantics.

## Plan
1. Add a transport shutdown hook in BaseTunnel.
   - Implement `_close_transport` as a no-op in `sfb/tunnel/base_tunnel.py`.
   - Call `_close_transport` from `BaseTunnel.close()` after stopping the
     background thread and module loader but before logging the final closed
     event, preserving current shutdown order as closely as possible.
   - Ensure exceptions are suppressed in the hook so shutdown remains best
     effort.

2. Move transport shutdown into subclass hooks.
   - Replace `AliceTunnel.close` and `BobTunnel.close` overrides with
     `_close_transport` implementations that call `self._transport.close()`
     and suppress exceptions.
   - Keep any existing side-specific cleanup in Base or the new hook so the
     Base close path remains the single entry point.

3. Remove subclass close overrides and verify call sites.
   - Delete the `close` methods from `sfb/tunnel/alice_tunnel.py` and
     `sfb/tunnel/bob_tunnel.py`.
   - Ensure all callers use `BaseTunnel.close()` without behavior changes.

4. Update the asymmetry documentation.
   - In `doc/architecture/ASYMMETRY.md`, add a section describing:
     - Shared BaseTunnel responsibilities (send helpers, decode/process path,
       handshake initialization, shutdown hook).
     - Alice-specific responsibilities (poll pacing, RTT-based retransmit,
       handshake driving).
     - Bob-specific responsibilities (request/response handling, wall-clock
       timeouts, opportunistic retransmit).
   - Explicitly call out that MTU negotiation remains asymmetric and that Bob
     throughput is bounded by Alice polling.

5. Clean break verification.
   - Confirm no unused send/receive/close helpers remain in Alice/Bob.
   - Update any references to subclass close methods to use the shared Base
     close path.

## References
- `doc/plans/BASE_TUNNEL_SYMMETRY_EXPANSION_PLAN.md`

## Testing
- Do not run tests.
