# Minimum Payload Guarantee Plan

Status: draft

## Goal

Enforce the new minimum payload requirement
(PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1) across TLS handshake
transports and tunnel initialization so too-small MTUs are rejected before
negotiation.

## Affected Components

- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/tunnel/base_tunnel.py
- sfb/protocol/constants.py (only if a shared minimum constant is added)

## Design Notes

- The minimum refers to the full packet size: packet header + segment header +
  at least 1 payload byte.
- Validate send and receive MTUs independently to preserve asymmetric
  negotiation.
- Fail fast before _init_transport_limits applies max(1, ...) and masks a
  too-small MTU.
- Keep error messages explicit about the minimum size requirement.

## Implementation Steps

1. Define the minimum packet size expression (local or shared constant) using
   PACKET_HEADER_SIZE and SEGMENT_HEADER_SIZE.
2. Update TLS handshake config validation to require client/server packet MTUs
   >= minimum packet size.
3. Update TLS handshake bump config validation to require SNI/CN packet MTUs
   >= minimum packet size.
4. In BaseTunnel._init_transport_limits, validate transport.send_packet_mtu
   and transport.recv_packet_mtu against the minimum and raise TunnelError if either
   is too small.
5. Update protocol/tunnel docs if they already describe minimum MTU behavior.

## Validation

- Run unit-level tests that cover tunnel init and TLS handshake validation
  using python3.
- Do not run tests in tests/e2e/.
