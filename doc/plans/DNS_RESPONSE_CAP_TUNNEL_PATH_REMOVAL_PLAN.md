# DNS Response Cap Tunnel Path Removal Plan

Status: draft

## Summary
Remove per-request DNS response payload caps from the tunnel path, keeping cap
validation and logging inside the DNS transport while the tunnel only uses the
negotiated send MTU.

## Goals
- Stop Bob tunnel logic from consulting `response_payload_cap` or related
  per-request DNS metadata.
- Keep DNS response cap computation and invariant checks inside DNS transport.
- Preserve existing fixed-cap clamping and MTU negotiation behavior.
- Update architecture docs to reflect that response caps are transport-only.

## Non-Goals
- Change fixed-cap calculations or DNS sizing logic.
- Modify non-DNS transports.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `doc/architecture/DNS_TRANSPORT.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Remove response-cap inputs from Bob response selection.
   - Drop `response_payload_cap` from `_select_response_action` and related
     call sites.
   - Remove `_log_response_cap` or limit it to transport-only logging.

2. Hide per-request cap metadata from the responder interface.
   - Stop exposing `response_payload_cap`, `qname_wire_len`, and
     `max_packet_size` on responder objects.
   - Keep per-request cap computation for DNS transport logging and invariant
     checks.

3. Update DNS transport responders to keep cap details local.
   - Ensure `_send_response` continues to log cap details without tunneling
     them into Bob.
   - Verify flat stager responses follow the same pattern.

4. Update architecture docs.
   - State that response caps are enforced within DNS transport and do not
     influence tunnel decisions.
   - Remove references to cap-blocked retransmits or per-request cap gating
     in the tunnel layer.

## Testing
- Do not run tests.
