# DNS Fixed Clamp Policy Plan

Status: draft

## Summary
Replace the DNS poll-hint clamp modes with a fixed response cap computed from
the worst-case CNAME response size under compression, and remove POLL_HINT
from the protocol. This is a breaking change; both sides must be upgraded
together.

## Related Plans
- `doc/plans/DNS_CNAME_COMPRESSION_PLAN.md` (compression raises response caps)

## Goals
- Remove the POLL_HINT flag and all poll-hint handling from protocol and
  tunnel logic.
- Use a fixed response payload cap based on the minimum CNAME response cap
  across all valid query payload sizes under compression.
- Clamp Bob's DNS send MTU and Alice's DNS recv MTU to the fixed response cap.
- Simplify DNS client clamp logic by removing per-send clamp modes and budgets.

## Non-Goals
- Add new flow-control signals or clamp hints.
- Change non-DNS transport behavior.
- Add or run automated tests.

## Affected Components
- `sfb/protocol/constants.py`
- `sfb/protocol/packet.py`
- `sfb/protocol/__init__.py`
- `sfb/tunnel/base_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_server.py`
- `doc/architecture/PROTOCOL.md`
- `doc/architecture/ASYMMETRY.md`
- `doc/architecture/TUNNEL.md`
- `doc/architecture/DNS_TRANSPORT.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/TRANSPORTS.md`
- `sfb_flat.py` (regenerate if shipped)

## Plan
1. Remove POLL_HINT from the protocol surface.
   - Delete `FLAG_POLL_HINT` from `sfb/protocol/constants.py` and exports.
   - Update `PacketHeader` in `sfb/protocol/packet.py` to drop poll-hint
     accessors, remove the flag from `_VALID_FLAGS`, and remove POLL_HINT from
     `__repr__`.
   - Update `sfb/tunnel/base_tunnel.py` validation and log fields to remove
     poll-hint checks and logging.

2. Remove poll-hint emission and plumbing in Bob.
   - In `sfb/tunnel/bob_tunnel.py`, stop OR-ing POLL_HINT on retransmits,
     keepalives, and segment responses.
   - Remove `poll_hint` fields from decision dictionaries and log payloads.
   - Rename any poll-hint specific drop/log reasons (for example,
     `poll_hint_window_full`) to neutral keepalive/window-full reasons.

3. Add a fixed response-cap helper for DNS CNAME responses.
   - Introduce a helper (in `sfb/transport/dns/dns_codec.py` or a shared
     utility) that computes the minimum response payload cap across all valid
     query payload lengths:
     - Iterate payload lengths from `MIN_PACKET_MTU` to the raw query MTU.
     - For each, compute `qname_wire_len` via `calc_qname_wire_len`.
     - Compute the response cap with `calc_cname_response_payload_cap`
       using compression parameters from the CNAME compression plan.
     - Track the smallest non-zero cap; return it along with the packet-size
       ceiling for logging.
   - If the minimum cap is below `MIN_PACKET_MTU`, raise a TransportError with
     base-domain, label, and EDNS sizing context.

4. Apply the fixed response cap in DNS client/server.
   - `sfb/transport/dns/dns_client.py`:
     - Remove poll-hint budget state, bob-data tracking, clamp modes, and
       response-cap lookup tables.
     - Compute the fixed response cap at init and clamp `_recv_packet_mtu` to
       `min(calculated_recv_mtu, fixed_response_cap)`.
     - Have `payload_cap_for_send()` return None and remove `_select_payload_cap`
       and `_attach_payload_cap` usage.
     - Replace clamp-selection logs with a single `dns.fixed_response_cap`
       log on init.
   - `sfb/transport/dns/dns_server.py`:
     - Replace `_compute_max_response_packet_mtu` with a minimum-cap variant
       and clamp `_send_packet_mtu` to the fixed cap.
     - When building responders, use `min(fixed_response_cap, per_query_cap)`
       if per-query caps are retained for logging, so oversize protection is
       preserved.
     - Add a log event for the fixed response cap and its inputs.

5. Update docs to remove poll-hint semantics and describe fixed clamp.
   - `doc/architecture/PROTOCOL.md`: remove POLL_HINT flag semantics, mark bit
     4 reserved.
   - `doc/architecture/ASYMMETRY.md`: remove poll-hint retransmit notes.
   - `doc/architecture/TUNNEL.md`: remove keepalive + poll-hint behavior.
   - `doc/architecture/DNS_TRANSPORT.md`: replace clamp-mode section with the
     fixed response-cap policy.
   - `doc/architecture/BOB_RETRANSMIT_LOGIC.md`: remove poll-hint references.
   - `doc/architecture/TRANSPORTS.md`: update the DNS MTU note to describe
     the fixed response cap and initialization failure conditions.

6. Regenerate flat build artifacts if they are shipped.
   - Run `python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py`.

## Testing
- Do not run tests.
