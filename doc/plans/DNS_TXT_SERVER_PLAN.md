# DNS TXT Server Plan

Status: draft

## Summary

Implement `sfb/transport/dns_txt/dns_txt_server.py` to provide the Bob-side
TXT transport, decoding TXT queries and sending TXT responses without EDNS0.

## Goals

- Implement a DNS TXT server that matches the Server transport interface.
- Keep `dns_txt` self-contained with no imports from `sfb.transport.dns`.
- Omit EDNS0 support entirely (no OPT records, 512-byte DNS responses).
- Preserve Python 2/3 compatibility and ASCII-only source.

## Non-Goals

- Implement client or codec logic.
- Add TXT stager support or recursion handling beyond base-domain matching.
- Register the transport, update CLI, or add tests.

## Affected Components

- `sfb/transport/dns_txt/dns_txt_server.py`
- `sfb/transport/dns_txt/dns_utils.py`
- `doc/plans/DNS_TXT_TRANSPORT_PLAN.md` (reference only)

## Plan

1. Copy DNS utility helpers.
   - Copy `sfb/transport/dns/dns_utils.py` verbatim to
     `sfb/transport/dns_txt/dns_utils.py` to avoid importing from
     `sfb.transport.dns`.
2. Configuration and socket setup.
   - Validate `Config` and capture `dns_base_domain`, `dns_label_max_len`,
     `dns_response_ttl`, and `dns_listen_addr`.
   - Parse `dns_listen_addr` with `parse_host_port`, create a UDP socket, and
     bind; on bind failure, call `raise_bind_error`.
   - Initialize the logger and emit `dns_txt.server_config`.
3. Disable EDNS0 explicitly.
   - Do not build OPT records or advertise EDNS0 sizes.
   - Ignore `dns_edns_size` for TXT; use `DNS_STANDARD_SIZE` (512) for sizing.
   - Set `_recv_bufsize` to `max(DNS_STANDARD_SIZE, dns_recv_bufsize_min)` for
     socket reads, but treat 512 as the protocol response cap.
4. MTU resolution and response caps.
   - Call `resolve_mtu_limits('dns_txt', config, role='server')` to set
     `send_packet_mtu` and `recv_packet_mtu` (using 512-byte TXT response MTU).
   - Log `transport.mtu_limits` with constraints from the MTU resolver.
5. Query parsing and filtering.
   - Implement `_parse_query` to validate DNS headers, decode QNAME, and return
     `(query_id, qname, qtype)`; reject non-queries, missing questions, bad
     class, or malformed names.
   - In `recv`, ignore queries outside `dns_base_domain`.
   - If `qtype` is not TXT, send an empty NOERROR response with `reason` and
     continue.
6. TXT payload decoding and responder creation.
   - Decode the query payload from `qname` using `dns_txt_codec`.
   - Create a responder that sends a TXT answer with the decoded payload cap
     from `dns_txt_codec.calc_response_mtu(QTYPE_TXT, DNS_STANDARD_SIZE)`.
   - Emit `dns_txt.recv` logs with `dns_id`, `qtype`, query sizes, and cap.
7. Response helpers.
   - Implement `_send_response` to build a TXT answer with name compression,
     QCLASS IN, TTL from `dns_response_ttl`, and TXT RDATA.
   - Enforce the 512-byte response size; if the TXT RDATA would overflow,
     log and raise `TransportError`.
   - Implement `_send_empty_response` to return NOERROR with no answers;
     optionally include a minimal SOA record (TTL=0) to avoid negative caching,
     built locally in `dns_txt_server.py` using `dns_txt_codec` helpers.
8. Cleanup.
   - Implement `close()` to close the UDP socket.

## Cross-References

- Parent plan: `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.

## Testing

- Do not run tests.
