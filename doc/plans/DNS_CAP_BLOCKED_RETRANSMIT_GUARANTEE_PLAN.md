# DNS Cap-Blocked Retransmit Guarantee Plan

Status: draft

## Summary
Guarantee that Bob never hits a cap-blocked retransmit by enforcing invariant
response caps at responder creation and treating any cap mismatch as fatal,
then updating documentation to reflect the new guarantee.

## Goals
- Ensure `response_payload_cap` is always at least the fixed response cap and
  large enough for any tunnel packet Bob can send or retransmit.
- Make cap-blocked retransmit paths unreachable in normal operation.
- Treat any cap mismatch as a fatal transport error with clear logging.
- Document the guarantee in the architecture references.

## Non-Goals
- Change non-DNS transports.
- Redesign MTU negotiation.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_flat_stager.py`
- `sfb/tunnel/bob_tunnel.py`
- `doc/architecture/DNS_TRANSPORT.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Enforce invariant caps at DNS responder creation.
   - If a per-request `response_payload_cap` is below the fixed cap or below
     the effective send MTU, log and raise a fatal transport error.
   - Apply the same invariant check in the flat stager responder path.
2. Remove the keepalive fallback for cap-blocked retransmits.
   - Convert the cap-blocked branch in Bob's retransmit send path to a fatal
     error and close the tunnel if it ever triggers.
   - Add a dedicated log event for this fatal condition.
3. Confirm send MTU is always clamped to the fixed cap for DNS.
   - Add an assertion or explicit check that negotiated `send_packet_mtu` does
     not exceed the fixed cap after handshake updates.
4. Update architecture docs.
   - State the invariant and guarantee that cap-blocked retransmits cannot
     occur under the fixed clamp policy.
   - Note that any cap mismatch is treated as fatal configuration or path
     error.
5. Manual verification.
   - Start Bob with DNS transport and confirm logs show the invariant check and
     no cap-blocked retransmit warnings.

## Testing
- Do not run tests.
