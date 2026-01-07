# DNS Minimum Response Cap Plan

## Goal
- Ensure default DNS query sizing allows Bob to send at least MIN_PACKET_MTU
  bytes in responses without waiting for POLL_HINT.
- Fail fast when no query payload size can yield response_payload_cap >=
  MIN_PACKET_MTU for the configured base domain/labels.

## Non-Goals
- Change MTU negotiation rules or poll hint semantics.
- Modify non-DNS transports.
- Run tests here.

## Affected Components
- sfb/transport/dns/dns_client.py
- doc/architecture/DNS_TRANSPORT.md
- doc/architecture/TRANSPORTS.md

## Plan
1. In `DnsClient._init_response_caps`, compute the largest query payload that
   yields `response_payload_cap >= MIN_PACKET_MTU`.
2. If no payload length can satisfy the minimum response cap, raise a
   `TransportError` that reports the base domain, label size, and EDNS size.
3. Clamp the default query payload to the computed safe size (either by
   reducing `self._send_packet_mtu` or by introducing a default payload cap
   used before poll hints) and log the old/new values.
4. Update DNS transport docs to describe the minimum-response requirement and
   the fail-fast configuration error when the constraint cannot be met.

## Testing
- Do not run tests here; the user will run needed tests with python3.

## Execution Notes (20260107)
- Added a minimum response-cap clamp for DNS query MTU in DnsClient and a
  fail-fast TransportError when the minimum cap cannot be satisfied.
- Updated DNS transport documentation to describe the minimum-response
  requirement and configuration failure behavior.
- Tests not run (per instructions).
