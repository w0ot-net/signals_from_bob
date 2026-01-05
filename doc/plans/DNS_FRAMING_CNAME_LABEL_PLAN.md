# DNS Framing and CNAME Simplification Plan

## Goal
- Make DNS query/response payload sizes fixed with a length-prefix framing layer.
- Precompute a single response payload cap at transport init and remove per-query sizing work.
- Freeze the CNAME label to a constant and drop dns_cname_a_addr and follow-up handling.

## Non-Goals
- Change non-DNS transports or the reliability protocol.
- Add non-stdlib dependencies or drop Python 2.7/3 compatibility.
- Run E2E tests under tests/e2e (user will run them).

## Affected Components
- sfb/transport/dns/codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/dns/__init__.py
- sfb/transport/dns/dns_utils.py
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
   - Query frame size = calc_query_mtu(...) bytes.
   - Response frame size = min(calc_response_mtu(...),
     precomputed response payload cap based on fixed QNAME wire length and
     UDP size).
   - Data caps = frame_size - 2 for each direction.
3) Remove per-query response sizing in the server:
   - Delete DnsServer._response_payload_cap and related qname_wire_len and
     max_packet_size plumbing in _ResponseSender.
   - Use a constant response payload cap derived at init and attach it to the
     responder.
4) Apply framing in send/receive paths:
   - DnsClient: prefix length and pad outgoing data to the query frame size;
     decode framing on responses and return the exact payload length.
   - DnsServer: decode query framing before handing data to the tunnel; frame
     and pad response payloads before encoding the CNAME target.
   - Keep asymmetric send/recv MTU negotiation based on the per-direction data
     caps.
5) Freeze CNAME label and drop dns_cname_a_addr:
   - Replace config-driven cname label with a constant (e.g., "0").
   - Remove dns_cname_label and dns_cname_a_addr from Config and validation.
   - Remove CNAME follow-up A handling and related code paths; DNS transport
     becomes direct-mode only (no recursive resolver follow-ups).
   - Require dns_resolver for DNS client CLI/config when DNS transport is used.
6) Update documentation for framing and direct-only DNS behavior:
   - DNS_TRANSPORT: framing format, fixed sizes, and direct-only mode.
   - DNS_CNAME_SUFFIX: update rationale to constant label and no follow-ups.
   - TRANSPORTS/PROTOCOL: update any references to resolver behavior and
     response encoding.
7) Update tests:
   - Adjust DNS codec/client/server tests for framing and constant label.
   - Remove or repurpose dns_utils tests if authoritative mode support is
     removed.
   - Ensure DNS E2E direct-mode tests use port 5353; do not run them.
