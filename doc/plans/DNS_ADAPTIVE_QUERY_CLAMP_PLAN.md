# DNS Adaptive Query Clamp and Payload Cap Removal Plan

## Goal
- Keep per-query DNS sizing while guaranteeing minimum response capacity.
- Dynamically clamp Alice query payloads when Bob has data to send, without
  forcing fixed framing.
- Remove payload_cap from the transport/tunnel interface while preserving a
  per-packet clamp hook so Alice can still shrink requests when DNS needs it.
- Preserve per-request response caps on Bob so DNS responses do not exceed
  what each query can carry.

## Non-Goals
- Introduce fixed framing or change the CNAME label format.
- Change non-DNS transports beyond removing payload_cap plumbing.
- Modify reliability semantics outside the clamp/MTU enforcement described.
- Run E2E tests under tests/e2e (user will run them).

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/transport_base.py
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/transport/udp_ephemeral/udp_ephemeral_server.py
- doc/DNS_TRANSPORT.md
- doc/BOB_RETRANSMIT_LOGIC.md
- doc/TRANSPORTS.md
- doc/PROTOCOL.md
- doc/UDP_EPHEMERAL_TRANSPORT.md
- tests/test_dns_client.py
- tests/test_dns_server.py
- tests/test_dns_codec.py
- tests/test_tunnel.py
- tests/test_bob_tunnel.py

## Design Notes
- Keep a per-request response cap on Bob for DNS so responses never exceed the
  size derived from the specific query, even with pipelined requests or
  retransmits before Alice's clamp updates.
- Ensure Alice enforces the clamp at packet build time (not just at config
  validation). BaseTunnel._collect_segments must accept an explicit per-send
  payload cap or consult a transport callback so Alice can shrink queries
  before packing segments.
- Be explicit about units: transports and tunnels store packet MTUs, and
  payload bytes are derived as (packet_mtu - PACKET_HEADER_SIZE). DNS response
  caps are packet bytes, so convert by adding/subtracting PACKET_HEADER_SIZE
  to avoid off-by-header errors.

## Plan
1) Precompute query->response caps for DNS:
   - For each possible query payload length (0..max_query_payload),
     compute qname wire length and the corresponding response payload cap
     using base_domain, cname_suffix, label_max_len, and edns_size.
   - Build a lookup that answers: "largest query payload that still yields
     response_payload_cap >= target_response_payload".
   - Keep this in DnsClient for fast per-query clamp decisions.
2) Add adaptive clamp state in DnsClient:
   - Track a short "bob_has_data" window (e.g., a small poll countdown).
   - When a response arrives, treat payload length > PACKET_HEADER_SIZE as
     "segments present" and reset the countdown; otherwise decay/clear it.
3) Clamp query payloads based on Bob activity:
   - Define min_response_packet_mtu as
     PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1, and convert to payload
     bytes (min_payload = SEGMENT_HEADER_SIZE + 1) when comparing against
     tunnel MTUs.
   - If bob_has_data is set, target_response_payload should allow Bob to send
     up to the current response packet MTU (bounded by what DNS can encode).
   - Otherwise target_response_payload = min_response_packet_mtu.
   - Clamp outgoing query payload to the precomputed maximum for that target,
     while respecting the transport send_packet_mtu.
   - Expose the chosen clamp to the tunnel via a per-send payload cap so
     _collect_segments uses min(send_payload_limit, transport_cap) when
     packing.
   - Wire the clamp into Alice's send path (e.g., carry the cap on the
     transport SendPermit or a transport callback) and have AliceTunnel pass
     the per-send cap into _collect_segments instead of using
     send_payload_limit alone.
4) Preserve per-request response caps on Bob:
   - Continue computing response_payload_cap from each query (qname length,
     EDNS size) in DnsServer.
   - Attach the per-request cap to the responder and have BobTunnel enforce it
     for new responses and retransmits so packets never exceed the query's
     response size budget.
5) Enforce minimum response capacity at init:
   - If no query payload length yields response_payload_cap >=
     min_response_payload for the configured base_domain/edns/label_max_len,
     fail DNS init with a clear configuration error.
6) Replace payload_cap with explicit clamp hooks:
   - Keep BaseTunnel per-packet clamping but drive it from a new transport
     callback/property that can vary per request (DNS uses adaptive clamp,
     other transports return None).
   - Keep BobTunnel per-response cap enforcement (rename the field if
     payload_cap is removed from the transport interface) for both new sends
     and retransmits.
   - Stop attaching payload_cap as a transport attribute; keep per-request
     response caps on DNS responders and rely on MTU/DNS sizing elsewhere.
7) Update documentation:
   - DNS_TRANSPORT: describe adaptive clamp behavior and response-cap rules.
   - PROTOCOL/TRANSPORTS: only update if new clamp behavior is documented or
     payload_cap references are added elsewhere.
   - BOB_RETRANSMIT_LOGIC and UDP_EPHEMERAL_TRANSPORT: remove payload_cap
     references tied to the old interface.
8) Update tests (non-e2e):
   - DNS client/server/codec tests for clamp lookup and min-cap enforcement.
   - Tunnel/BobTunnel tests that reference payload_cap behavior.
