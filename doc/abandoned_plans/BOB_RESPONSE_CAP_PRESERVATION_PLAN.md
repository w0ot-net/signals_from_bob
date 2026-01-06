# Bob Response Cap Preservation Plan

Status: abandoned

## Goal
- Preserve per-request response caps on Bob when payload_cap plumbing is removed
  or renamed elsewhere.
- Keep DNS per-query caps enforced for new sends and retransmits.
- Clarify responder metadata so future refactors do not drop enforcement.

## Non-Goals
- Change MTU negotiation, clamp math, or retransmit/asymmetry behavior.
- Add new transport features beyond cap plumbing.
- Update or run tests in tests/e2e/.

## Affected Components
- sfb/tunnel/bob_tunnel.py
- sfb/transport/dns/dns_server.py
- sfb/transport/udp_ephemeral/udp_ephemeral_server.py
- sfb/transport/transport_base.py
- doc/BOB_RETRANSMIT_LOGIC.md
- doc/DNS_TRANSPORT.md
- doc/TRANSPORTS.md
- doc/UDP_EPHEMERAL_TRANSPORT.md

## Plan
1) Define a responder response-cap contract:
   - Document an optional responder attribute `response_payload_cap` in packet
     bytes for per-request caps.
   - Call out that this is separate from the client-side
     `payload_cap_for_send` hook.
2) Update Bob to use the response-cap contract:
   - Replace any `responder.payload_cap` usage with
     `responder.response_payload_cap`.
   - Keep enforcement for both new sends and retransmits, including the
     poll-hint control segment path when a cap blocks retransmit.
3) Update transports to attach per-request caps:
   - DNS server: ensure `_ResponseSender` sets `response_payload_cap` and
     retains `qname_wire_len`/`max_packet_size` for logging.
   - UDP ephemeral: decide whether to attach a fixed
     `response_payload_cap = send_packet_mtu` for logging consistency or leave
     the attribute absent; document the chosen behavior.
4) Align documentation:
   - Update `doc/BOB_RETRANSMIT_LOGIC.md` and `doc/DNS_TRANSPORT.md` to reference
     `response_payload_cap`.
   - Update the server-side section of `doc/TRANSPORTS.md` to mention the
     optional responder response cap.
   - Update `doc/UDP_EPHEMERAL_TRANSPORT.md` to reflect the chosen responder
     cap behavior.
5) Validation:
   - Run relevant non-e2e unit tests with `python3` if desired.
   - Do not run tests under `tests/e2e/`.

## Abandon Notes
- Superseded by later guidance; leaving the plan here for reference only.
