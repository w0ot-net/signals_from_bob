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
- Optimize or drain pipelined in-flight DNS queries when clamp state changes;
  a short lag is acceptable as long as per-request caps prevent oversize
  responses and Bob can still send at least one segment.
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
- Do not add special handling for in-flight pipelined queries when bob_has_data
  flips; the transient lag is acceptable because per-request caps guarantee
  responses stay within the query budget and allow Bob to deliver at least one
  segment, which is sufficient to inform Alice quickly.
- Ensure Alice enforces the clamp at packet build time (not just at config
  validation). BaseTunnel._collect_segments must accept an explicit per-send
  payload cap or consult a transport callback so Alice can shrink queries
  before packing segments.
- Be explicit about units: transports and tunnels store packet MTUs, and
  payload bytes are derived as (packet_mtu - PACKET_HEADER_SIZE). DNS response
  caps are packet bytes, so convert by adding/subtracting PACKET_HEADER_SIZE
  to avoid off-by-header errors.
- Removing payload_cap requires a replacement that still supports per-send
  clamping; otherwise Alice will always pack to its negotiated _send_packet_mtu
  and DNS can only reject oversize requests, not resize them. Keep the clamp
  at packet build time via a per-send cap sourced from the transport.
- Ensure the max achievable DNS response packet size is at least the negotiated
  response MTU. calc_response_mtu is optimistic for CNAME because it ignores
  EDNS size and qname length; use the lookup to derive an actual cap and either
  clamp recv/send MTUs to it or fail init when it falls below the minimum
  response packet size.
- Prevent retransmit-cap deadlocks when payload_cap is removed: do not
  hard-close if a retransmit exceeds the current request cap, and keep the
  clamp hot across loss so Alice continues issuing small queries until Bob
  successfully retransmits.

## Plan
1) Precompute query->response caps for DNS (DnsClient init):
   - For each possible query payload length (0..max_query_payload), compute:
     - QNAME wire length for the encoded payload and base_domain.
     - Response payload cap (packet bytes) by calling a shared codec helper
       that mirrors server sizing, including EDNS clamp and OPT record length.
   - Store a lookup that answers: "largest query payload that still yields
     response_payload_cap >= target_response_payload".
   - Record max_response_payload_cap as the maximum response_payload_cap across
     all query payload lengths, and derive max_response_packet_mtu as
     PACKET_HEADER_SIZE + max_response_payload_cap.
   - Implement the response-cap helper in sfb/transport/dns/codec.py and use it
     in both DnsClient and DnsServer:
     - Helper inputs: qname_wire_len, edns_size, cname_suffix, label_max_len,
       opt_record_len (or an edns_enabled flag that implies opt_record_len).
     - Helper outputs: (response_payload_cap, max_packet_size).
     - Helper must apply the same EDNS clamp and OPT record length rules as
       DnsServer._response_payload_cap so the client lookup cannot diverge.
   - Keep the lookup in DnsClient so per-send clamp selection is O(1) and does
     not depend on live socket state.
2) Enforce a response MTU ceiling derived from the lookup:
   - Compute max_response_packet_mtu from the lookup (step 1).
   - If max_response_packet_mtu < min_response_packet_mtu
     (PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1), fail DNS init with a clear
     configuration error (base_domain/label_max_len/edns_size too restrictive).
   - Clamp DNS response MTUs to max_response_packet_mtu:
     - DnsClient.recv_packet_mtu = min(calc_response_mtu(...),
       max_response_packet_mtu).
     - DnsServer.send_packet_mtu = min(calc_response_mtu(...),
       max_response_packet_mtu).
   - Log a dns.mtu_clamp event when clamping occurs with fields for
     calculated_mtu, max_response_packet_mtu, and effective_mtu so operators
     can see when DNS sizing constraints reduce tunnel MTU.
3) Track adaptive clamp state in DnsClient:
   - Maintain a "bob_has_data" countdown (poll budget) that decays on each
     keepalive-only response; reset to a small fixed number when any response
     contains segments.
   - Define "segments present" as response_payload_len >
     PACKET_HEADER_SIZE (consistent with tunnel segment presence semantics).
   - Do not decay bob_has_data on timeouts or missing responses; only decay on
     explicit keepalive-only responses so loss cannot prematurely relax the
     clamp while Bob may still need to retransmit.
   - Keep this state independent of MTU negotiation so it only controls the
     clamp target, not the negotiated send/recv MTUs.
4) Decide the target response size for each send:
   - Compute min_response_packet_mtu as
     PACKET_HEADER_SIZE + SEGMENT_HEADER_SIZE + 1.
   - Convert to payload bytes when comparing against per-send payload caps:
     min_response_payload = SEGMENT_HEADER_SIZE + 1.
   - If bob_has_data is set, target_response_payload should allow Bob to send
     up to the current response packet MTU (bounded by what DNS can encode).
   - Otherwise target_response_payload = min_response_packet_mtu to keep
     response slots small while idle.
5) Select and attach the per-send clamp (packet bytes):
   - Use the precomputed lookup to pick the largest query payload length whose
     response payload cap >= target_response_payload.
   - Convert that query payload length into a packet cap by adding
     PACKET_HEADER_SIZE, and clamp it to transport.send_packet_mtu.
   - If no payload length satisfies min_response_payload, the DNS transport
     must fail initialization with a clear configuration error.
6) Plumb the per-send clamp into the tunnel send path:
   - Introduce a transport hook for per-send caps, e.g.
     Transport.payload_cap_for_send(permit) -> packet bytes or None.
   - Default implementation returns None so non-DNS transports are unchanged.
   - In AliceTunnel, after reserve_send(), call the transport hook and pass
     the returned cap into BaseTunnel._collect_segments (new optional arg).
   - Ensure BaseTunnel clamps max_payload as
     min(max_payload, payload_cap - PACKET_HEADER_SIZE) when a cap is given.
7) Implement the DNS side of the hook:
   - In DnsClient.reserve_send(), compute the per-send cap from the adaptive
     clamp rules and attach it to the SendPermit (e.g., permit.data).
   - Implement DnsClient.payload_cap_for_send(permit) to read the attached
     value and return it as packet bytes.
   - If DnsClient is wrapped (lossy transport), forward the cap via the inner
     permit so the wrapper can delegate to the inner transport.
8) Preserve per-request response caps on Bob:
   - Continue computing response_payload_cap from each query in DnsServer.
   - Attach the per-request cap to the responder and have BobTunnel enforce it
     for new responses and retransmits so packets never exceed the query's
     response size budget.
9) Add a retransmit-cap guard before removing payload_cap:
   - In BobTunnel._send_retransmit_response, if response_data exceeds the
     current request's response_payload_cap:
     - Log a distinct event (e.g., tunnel.retransmit_cap_blocked) with
       seq/bytes/cap so operators can diagnose cap mismatches.
     - Do not close the tunnel; leave the unacked entry intact so it can be
       retransmitted when a larger-cap request arrives.
     - Send a small control-only response (poll hint) instead of a keepalive
       to ensure Alice sees a segment and keeps bob_has_data hot.
   - Ensure Alice treats any response with segments (including poll hints) as
     bob_has_data so the clamp stays in the "small query" mode until Bob
     retransmits successfully.
10) Remove payload_cap from the transport/tunnel interface:
   - Delete payload_cap attributes and any BaseTunnel state that caches it.
   - Keep per-send clamping exclusively through the new transport hook.
   - Keep Bob-side per-response cap enforcement (rename fields as needed) so
     retransmits still honor the original query budget.
11) Update documentation:
   - DNS_TRANSPORT: describe adaptive clamp behavior and the per-send cap hook.
   - PROTOCOL/TRANSPORTS: update if payload_cap references exist or if the new
     hook is part of the public transport contract.
   - BOB_RETRANSMIT_LOGIC and UDP_EPHEMERAL_TRANSPORT: remove payload_cap
     references tied to the old interface.
12) Update tests (non-e2e):
   - DNS client/server/codec tests for clamp lookup, per-send caps, and
     min-cap enforcement.
   - Tunnel/BobTunnel tests that reference payload_cap behavior.
