# DNS Cap-Blocked Retransmit Guarantee Plan

Status: completed

## Summary
Guarantee that cap-blocked retransmit paths are eliminated entirely by making
response caps invariant (always large enough for any retransmit) and removing
the cap-blocked branch from Bob.

## Goals
- Ensure `response_payload_cap` is always large enough for any tunnel packet
  Bob can send or retransmit.
- Remove the cap-blocked retransmit branch so it no longer exists.
- Treat any cap mismatch as a fatal transport error before a responder is used.
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
   - Require per-request `response_payload_cap` to be >= the fixed cap and the
     effective send MTU, log and raise a fatal transport error otherwise.
   - Apply the same invariant check in the flat stager responder path.
2. Eliminate the cap-blocked retransmit path.
   - Remove the cap check/keepalive fallback in Bob's retransmit send path.
   - Remove the cap-blocked log events that become unreachable.
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

## Execution Notes
- Enforced invariant DNS response caps for tunnel and flat stager responses with
  fatal checks on cap mismatches.
- Removed the cap-blocked retransmit fallback from Bob and added send MTU
  validation after MTU negotiation updates.
- Updated architecture docs to document invariant response caps and the
  removal of cap-blocked retransmits.
- Manual verification not run (per instructions).
