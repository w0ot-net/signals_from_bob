# DNS Fixed Clamp Policy Phase 3 - Documentation and Flat Build

Status: draft

## Summary
Update documentation to remove POLL_HINT semantics and describe the fixed DNS
response cap, then regenerate flat build artifacts if they are shipped.

## Dependencies
- Requires Phase 1 and Phase 2 so docs reflect the implemented behavior.

## Goals
- Remove all POLL_HINT references from protocol and tunnel documentation.
- Document the fixed response cap policy and failure conditions.
- Update DNS MTU notes to reflect fixed-cap initialization.
- Regenerate sfb_flat.py if shipped.

## Non-Goals
- Code changes beyond documentation and optional flat regeneration.
- Running tests.

## Affected Components
- doc/architecture/PROTOCOL.md
- doc/architecture/ASYMMETRY.md
- doc/architecture/TUNNEL.md
- doc/architecture/DNS_TRANSPORT.md
- doc/architecture/BOB_RETRANSMIT_LOGIC.md
- doc/architecture/TRANSPORTS.md
- sfb_flat.py (regenerate if shipped)

## Plan
1. Protocol documentation
   - Remove POLL_HINT bit definition and set bit 4 as reserved.
   - Update content-flag constraints to remove POLL_HINT references.

2. Asymmetry and retransmit docs
   - Remove POLL_HINT mentions in retransmit descriptions.
   - Update retransmit blocked behavior to describe keepalive responses without
     poll hints.

3. Tunnel keepalive docs
   - Remove keepalive + poll-hint behavior and clamp references.
   - Ensure keepalive semantics describe only KEEPALIVE and HAS_SEGMENTS.

4. DNS transport docs
   - Replace the clamp-mode section with the fixed response cap policy:
     - Describe fixed-cap computation (minimum cap across valid query payload
       sizes under compression).
     - Explain that Alice recv_packet_mtu and Bob send_packet_mtu are clamped to
       the fixed cap.
     - Document failure conditions when compression cannot be applied or the
       fixed cap is below MIN_PACKET_MTU.
   - Remove poll-hint budgets and clamp mode descriptions.
   - Update the MTU examples table to reflect the fixed-cap regime or add a
     note clarifying the updated meaning of response caps.

5. Transport overview docs
   - Update DNS MTU notes to describe fixed response cap behavior and
     initialization failure conditions.

6. Flat build artifacts
   - If sfb_flat.py is shipped, regenerate with:
     python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py
   - Do not modify code under ./tests.

## Testing
- Do not run tests.
