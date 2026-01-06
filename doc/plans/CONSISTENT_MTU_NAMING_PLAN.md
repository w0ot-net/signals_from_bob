# Consistent MTU Naming Plan

Status: draft

## Goal

Make MTU naming consistent and unambiguous across config, CLI, code, and docs
by separating on-wire packet sizes from tunnel payload sizes. This is a
breaking change, including CLI/config renames, with all call sites updated.

## Non-Goals

- Change MTU semantics, negotiation rules, or payload sizing logic.
- Preserve backward compatibility for old CLI flags or config keys.
- Run E2E tests under tests/e2e (user will run them).

## Affected Components

- sfb/config.py
- sfb/cli.py
- sfb/transport/transport_base.py
- sfb/transport/* (icmp, udp_ephemeral, dns, tls, tls_bump, memory, lossy)
- sfb/tunnel/base_tunnel.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/tunnel_control_messages.py
- sfb/protocol/* (constants and docs references)
- doc/TRANSPORTS.md
- doc/DNS_TRANSPORT.md
- doc/ICMP_TRANSPORT.md
- doc/UDP_EPHEMERAL_TRANSPORT.md
- doc/TLS_TRANSPORT.md
- doc/TUNNEL.md
- doc/PROTOCOL.md
- tests/* (non-e2e references to MTU names)

## Naming Scheme

- packet_mtu = on-wire packet bytes (packet header + segments).
- payload_mtu = tunnel payload bytes (packet_mtu - PACKET_HEADER_SIZE).
- Record-size settings keep "bytes" naming (TLS record size is not a tunnel MTU).

## Plan

1) Define a naming map and remove legacy names:
   - Config: icmp_payload_mtu -> icmp_packet_mtu
   - Config: udp_ephemeral_payload_mtu -> udp_ephemeral_packet_mtu
   - Config: protocol_max_packet_size -> protocol_max_packet_mtu
   - Config: protocol_initial_mtu -> protocol_initial_payload_mtu
   - CLI: --icmp-mtu -> --icmp-packet-mtu
   - CLI: --udp-ephemeral-mtu -> --udp-ephemeral-packet-mtu
   - CLI: --tls-mtu -> --tls-max-record-bytes (keep TLS record terminology)
   - Update any doc examples and config snippets to match the new names.
2) Rename transport MTU properties to packet units:
   - Transport.send_mtu/recv_mtu -> send_packet_mtu/recv_packet_mtu.
   - Update all transports to expose packet MTUs with the new names.
   - Update call sites to use the new properties.
3) Rename tunnel MTU state to payload units:
   - BaseTunnel _send_mtu/_recv_mtu/_default_mtu ->
     _send_payload_mtu/_recv_payload_mtu/_default_payload_mtu.
   - Proposed/pending MTU fields get payload naming as well.
   - negotiated_mtu -> negotiated_payload_mtu, and align related accessors.
   - Update Alice/Bob tunnel code to use the new payload naming.
4) Keep MTU control messages consistent with payload units:
   - Keep on-wire tun_mtu fields as payload bytes but rename helpers/locals
     to payload_mtu terminology to avoid unit confusion.
5) Update docs to match the new naming:
   - Explicitly map transport packet MTU to tunnel payload MTU.
   - Align terminology in TLS_TRANSPORT, PROTOCOL, and TUNNEL.
   - Ensure transport docs describe packet MTUs and tunnel docs describe
     payload MTUs with the same words as code/config/CLI.
6) Update tests (non-e2e) and any fixtures/config parsing to use new names.

## Validation

- Run unit tests that cover config parsing, transport init, and tunnel MTU
  negotiation using python3.
- Do not run tests in tests/e2e/.
