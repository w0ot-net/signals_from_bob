# DNS Fixed Clamp Policy Phase 2 - Fixed Response Cap Implementation

Status: draft

## Summary
Replace adaptive poll-hint clamp behavior with a fixed DNS response payload cap
computed from the worst-case CNAME response size under compression, then apply
that cap in DNS client and server initialization.

## Dependencies
- Requires Phase 1 so POLL_HINT is removed before the fixed-cap logic lands.
- Depends on doc/plans/DNS_CNAME_COMPRESSION_PLAN.md for compression sizing.

## Goals
- Add a helper that computes the minimum CNAME response payload cap across all
  valid query payload sizes, using compression only.
- Clamp Alice recv_packet_mtu and Bob send_packet_mtu to the fixed cap.
- Remove DNS client clamp modes, poll-hint budgets, and bob-data tracking.
- Preserve per-request response cap protection by using
  min(fixed_cap, per_query_cap) for responders.

## Non-Goals
- Changes to non-DNS transports.
- New flow-control flags or clamp hints.
- Stager behavior changes beyond fixed-cap logging (if needed).

## Affected Components
- sfb/transport/dns/dns_codec.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py

## Plan
1. Add fixed-cap helper in dns_codec
   - Add a helper (name TBD, for example calc_fixed_cname_response_payload_cap)
     that accepts:
     - raw_query_packet_mtu
     - edns_size
     - cname_suffix
     - base_domain
     - label_max_len
     - opt_record_len
   - Iterate payload lengths from MIN_PACKET_MTU to raw_query_packet_mtu.
   - For each payload length:
     - Compute qname_wire_len via calc_qname_wire_len.
     - Compute response cap via calc_cname_response_payload_cap with
       use_compression=True.
   - Treat compression as mandatory:
     - If compression cannot be applied for the base_domain/cname_suffix
       combination, raise TransportError. If needed, factor out a compression
       viability helper so the fixed-cap helper can detect this reliably.
   - Track the smallest non-zero response cap and remember the payload length
     and qname_wire_len that produced it for logging.
   - Return a tuple (fixed_cap, max_packet_size, min_payload_len,
     min_qname_wire_len) or an equivalent small struct for logging.
   - If the smallest non-zero cap is below MIN_PACKET_MTU, raise TransportError
     with base_domain, cname_suffix, label_max_len, edns_size, and
     raw_query_packet_mtu context.

2. DNS client initialization changes
   - Remove all poll-hint budget, bob-data tracking, and clamp mode state:
     - Fields: _alice_has_data_pending, _bob_has_data_*, _poll_hint_*,
       _recv_window_sack, _response_cap_lookup, _max_response_payload_cap,
       _safe/_unsafe/_balanced_query_payload, _retransmit_guard, and clamp
       logging state.
     - Methods: notify_send_pending, notify_peer_data, notify_recv_window_sack,
       _update_bob_data_from_payload, _select_payload_cap, _attach_payload_cap,
       _reset_poll_hint_budget, _consume_poll_hint_budget,
       _log_clamp_header_skip, and _log_unsafe_fallback.
     - Remove call sites in reserve_send() and _try_recv().
   - Compute fixed_response_cap during __init__ when rtype is CNAME and clamp
     _recv_packet_mtu to min(calculated_recv_mtu, fixed_response_cap).
   - Keep _send_packet_mtu as the raw query MTU (no per-send clamps).
   - payload_cap_for_send() returns None unconditionally.
   - Replace clamp-selection logs with a single dns.fixed_response_cap event
     that reports inputs and derived values.

3. DNS server initialization and responder changes
   - Replace _compute_max_response_packet_mtu with a minimum-cap helper call and
     clamp _send_packet_mtu to min(calculated_send_mtu, fixed_response_cap).
   - Keep per-query response cap calculation for logging/oversize protection,
     but pass min(fixed_response_cap, per_query_cap) to responders so
     oversize protection remains.
   - Add a dns.fixed_response_cap log event on init with inputs and derived
     values.

4. Error handling and invariants
   - Ensure TransportError messages include base_domain, cname_suffix,
     label_max_len, edns_size, and raw_query_packet_mtu.
   - Compression-required failures are fatal and should be logged as
     configuration/compat errors.

## Testing
- Do not run tests.
