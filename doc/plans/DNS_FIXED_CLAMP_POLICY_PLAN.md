# DNS Fixed Clamp Policy Plan

Status: draft

## Summary
Replace the DNS poll-hint clamp modes with a fixed response cap computed from
worst-case CNAME response size under compression, and remove POLL_HINT from the
protocol. This is a breaking change; both sides must be upgraded together.

## Related Plans
- doc/plans/DNS_CNAME_COMPRESSION_PLAN.md (compression raises response caps)

## Phase Documents
- Phase 1: Protocol and tunnel POLL_HINT removal
  - doc/plans/DNS_FIXED_CLAMP_POLICY_PHASE1_PROTOCOL.md
- Phase 2: Fixed DNS response cap implementation
  - doc/plans/DNS_FIXED_CLAMP_POLICY_PHASE2_DNS_FIXED_CAP.md
- Phase 3: Documentation updates and flat build regeneration
  - doc/plans/DNS_FIXED_CLAMP_POLICY_PHASE3_DOCS_FLAT.md

## Phase Order
- Phase 1 -> Phase 2 -> Phase 3.
- Phase 2 depends on DNS_CNAME_COMPRESSION_PLAN.md.

## Goals
- Remove the POLL_HINT flag and all poll-hint handling from protocol and tunnel
  logic.
- Use a fixed response payload cap based on the minimum CNAME response cap
  across all valid query payload sizes under compression.
- Clamp Bob's DNS send MTU and Alice's DNS recv MTU to the fixed response cap.
- Simplify DNS client clamp logic by removing per-send clamp modes and budgets.

## Non-Goals
- Add new flow-control signals or clamp hints.
- Change non-DNS transport behavior.
- Add or run automated tests.

## Affected Components (Aggregate)
- sfb/protocol/constants.py
- sfb/protocol/packet.py
- sfb/protocol/__init__.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/transport/dns/dns_codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- doc/architecture/PROTOCOL.md
- doc/architecture/ASYMMETRY.md
- doc/architecture/TUNNEL.md
- doc/architecture/DNS_TRANSPORT.md
- doc/architecture/BOB_RETRANSMIT_LOGIC.md
- doc/architecture/TRANSPORTS.md
- sfb_flat.py (regenerate if shipped)

## Plan
- Execute the phase documents in order and keep each phase scoped to its
  listed components.

## Testing
- Do not run tests.
