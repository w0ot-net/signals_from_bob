# DNS Framing and CNAME Simplification Plan

## Goal
- Make DNS query/response payload sizes fixed with a length-prefix framing layer.
- Precompute a single response payload cap at transport init and remove per-query sizing work.
- Freeze the CNAME label to a constant while preserving authoritative-mode resolver handling.

## Non-Goals
- Change non-DNS transports or the reliability protocol.
- Add non-stdlib dependencies or drop Python 2.7/3 compatibility.
- Run E2E tests under tests/e2e (user will run them).
- Drop authoritative DNS mode or resolver-based operation.

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/dns/__init__.py
- sfb/transport/dns/dns_utils.py
- sfb/tunnel/bob_tunnel.py
- sfb/config.py
- sfb/cli.py
- doc/DNS_TRANSPORT.md
- doc/DNS_CNAME_SUFFIX.md
- doc/TRANSPORTS.md
- doc/PROTOCOL.md
- tests/test_dns_codec.py
- tests/test_dns_client.py
- tests/test_dns_server.py
- tests/test_dns_utils.py
- tests/e2e/test_dns_e2e.py
- tests/e2e/test_dns_e2e_lossy.py

## Plan
1) Define DNS framing for tunnel payloads: 2-byte big-endian length prefix +
   payload + zero padding to a fixed frame size.
2) Compute fixed frame sizes once at transport init:
   - Define min_frame = PACKET_HEADER_SIZE + 2 + 4. This allows at least one
     control-channel byte per packet (3-byte segment header + 1 data byte) so
     MTU/window messages can stream even when framing is tight.
   - Query frame size: choose the largest q_frame <= calc_query_mtu(...)
     that still allows a response frame >= min_frame. Compute qname wire
     length for each candidate q_frame, then compute response_payload_cap
     from that fixed qname length.
   - Response frame size = min(calc_response_mtu(...),
     precomputed response payload cap based on fixed QNAME wire length and
     UDP size).
   - Data caps = frame_size - 2 for each direction.
   - Enforce a base domain length cap by running the same feasibility check
     at init time; reject configs where no q_frame yields both frames >=
     min_frame. For default label_max_len=50 and EDNS=512 with min_frame=44,
     the max base_domain length is 83 characters (including dots).
   - Set protocol_initial_mtu for DNS to the largest safe payload size:
     max(1, min(query_data_cap, response_data_cap) - PACKET_HEADER_SIZE).
     This makes the pre-negotiation MTU match the framing limits and avoids a
     hardcoded 100-byte default for DNS.
3) Remove per-query response sizing in the server:
   - Delete DnsServer._response_payload_cap and related qname_wire_len and
     max_packet_size plumbing in _ResponseSender.
   - Use a constant response payload cap derived at init and attach it to the
     responder.
   - Either keep responder.qname_wire_len/max_packet_size for BobTunnel
     logging or update BobTunnel to drop those fields.
4) Apply framing in send/receive paths:
   - DnsClient: prefix length and pad outgoing data to the query frame size;
     decode framing on responses and return the exact payload length.
   - DnsServer: decode query framing before handing data to the tunnel; frame
     and pad response payloads before encoding the CNAME target.
   - Keep asymmetric send/recv MTU negotiation based on the per-direction data
     caps.
5) Freeze CNAME label while keeping authoritative mode:
   - Replace config-driven cname label with a constant (e.g., "0").
   - Remove dns_cname_label from Config and validation.
   - Keep dns_cname_a_addr and CNAME follow-up A handling for recursive resolver
     follow-ups in authoritative mode.
   - Keep dns_resolver optional; retain system resolver loading for authoritative
     mode.
6) Update documentation for framing and constant-label DNS behavior:
   - DNS_TRANSPORT: framing format, fixed sizes, and both direct + authoritative
     modes.
   - DNS_CNAME_SUFFIX: update rationale to constant label; keep follow-up
     handling details for resolvers.
   - TRANSPORTS/PROTOCOL: update any references to resolver behavior and
     response encoding.
7) Update tests:
   - Adjust DNS codec/client/server tests for framing and constant label.
   - Update dns_utils tests only if resolver behavior changes; keep
     authoritative-mode coverage.
   - Ensure DNS E2E direct-mode tests use port 5353; do not run them.
