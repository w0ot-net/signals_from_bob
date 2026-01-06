# Tunnel Performance Improvements Phase 3 Plan

## Goal
- Make POLL_HINT imply capacity for at least 1 byte of segment payload.
- Remove ack-only responses and treat empty responses as idle keepalives only.
- Clarify protocol semantics for "real data" and POLL_HINT behavior.

## Non-Goals
- Change transport protocols, crypto behavior, or MTU/window negotiation rules.
- Modify end-to-end test coverage or run E2E tests.
- Alter reliability semantics beyond the protocol and transport adjustments described here.
- Add new packet header flags.

## Affected Components
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- doc/TUNNEL.md
- doc/ASYMMETRY.md
- doc/PROTOCOL.md
- doc/DNS_TRANSPORT.md

## Plan
1) Define a protocol requirement: when Bob sends POLL_HINT, the response must have capacity for at least 1 byte of segment payload. Enforce this per transport by ensuring the response payload cap is never less than PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1 when POLL_HINT is emitted. Transports with per-request caps (DNS) may produce smaller responses, but must not set POLL_HINT unless the cap meets this floor. If a transport can never reach this floor for the configured settings, fail fast during initialization (config error). With this guarantee, remove ack-only responses: Bob must never emit empty packets without KEEPALIVE, and receivers treat any empty/non-KEEPALIVE packet as a protocol violation.
2) Treat "real data" as the presence of segments (control or data), not the KEEPALIVE flag. Empty responses are idle keepalives only.
3) Update doc/TUNNEL.md, doc/ASYMMETRY.md, doc/PROTOCOL.md, and doc/DNS_TRANSPORT.md to document the "real data" definition, the minimum payload guarantee, and the removal of ack-only responses.
