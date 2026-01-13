# DNS TXT Client Plan

Status: draft

## Summary

Implement `sfb/transport/dns_txt/dns_txt_client.py` to provide the Alice-side
TXT transport, including resolver selection, query encoding, response decoding,
and pending tracking.

## Goals

- Implement a pipelined TXT client that matches the Transport interface.
- Keep the `dns_txt` client self-contained with no imports from
  `sfb.transport.dns`.
- Preserve Python 2/3 compatibility and ASCII-only source.

## Non-Goals

- Implement server or codec logic.
- Register the transport or update CLI wiring.
- Add or run tests.

## Affected Components

- `sfb/transport/dns_txt/dns_txt_client.py`
- `doc/plans/DNS_TXT_TRANSPORT_PLAN.md` (reference only)

## Plan

1. Configuration and resolver selection.
   - Validate `Config` instance and capture DNS fields needed by the client.
   - Parse `dns_resolver` if provided; otherwise implement a local resolver
     discovery helper in `dns_txt_client.py` for Unix and Windows (duplicate the
     minimal logic from `dns_utils` instead of importing it).
   - Log client configuration with `dns_txt.client_config`.
2. Socket and EDNS0 setup.
   - Create a non-blocking UDP socket.
   - Build and cache the OPT record when `dns_edns_size > 512` using
     `dns_txt_codec` helpers; store `_opt_record` and `_opt_arcount`.
   - Set `_recv_bufsize` to `max(dns_edns_size, dns_recv_bufsize_min)`.
3. MTU resolution.
   - Call `resolve_mtu_limits('dns_txt', config, role='client')`.
   - Store `send_packet_mtu` and `recv_packet_mtu` without CNAME-specific caps.
   - Emit `transport.mtu_limits` logging with the computed constraints.
4. Pending tracking and send path.
   - Mirror `DnsClient` behavior with `PendingTracker`, `SendPermit`, and a
     0x10000 DNS-ID lookup table for correlation IDs.
   - Implement `_encode_query`, `_next_query_id`, and `_build_query` using
     `dns_txt_codec` and local constants.
   - Enforce `send_packet_mtu` and log `dns_txt.send`.
5. Receive path.
   - Use `select` with deadline handling to read UDP responses.
   - Parse responses with `dns_txt_codec` helpers; validate response flags,
     qname match, rtype/QCLASS, and decode TXT payload.
   - Prune stale pending entries with `prune_and_count` and clear the DNS-ID
     mapping on completion.
   - Emit `dns_txt.recv` and error diagnostics for malformed or mismatched
     packets.
6. Cleanup and optional hooks.
   - Implement `close()` to clear pending state and close the socket.
   - Leave `payload_cap_for_send`, `notify_send_pending`, and
     `notify_recv_window_sack` as default no-ops unless the TXT design requires
     per-send caps.

## Cross-References

- Parent plan: `doc/plans/DNS_TXT_TRANSPORT_PLAN.md`.

## Testing

- Do not run tests.
