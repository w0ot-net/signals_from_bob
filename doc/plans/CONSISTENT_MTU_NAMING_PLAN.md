# Consistent MTU Naming Plan

Status: draft

## Goal

Make MTU naming consistent and unambiguous across config, CLI, code, and docs
by using packet_mtu as the sole stored MTU unit and deriving payload bytes at
boundaries. This is a breaking change, including CLI/config renames, with all
call sites updated.

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
- payload bytes are derived as (packet_mtu - PACKET_HEADER_SIZE) when needed;
  do not store payload_mtu as a variable or field.
- Record-size settings keep "bytes" naming (TLS record size is not a tunnel MTU).

## Plan

1) Define a naming map and remove legacy names:
   - Config: icmp_payload_mtu -> icmp_packet_mtu
   - Config: udp_ephemeral_payload_mtu -> udp_ephemeral_packet_mtu
   - Config: protocol_max_packet_size -> protocol_max_packet_mtu
   - Config: protocol_initial_mtu -> protocol_initial_packet_mtu
   - CLI: --icmp-mtu -> --icmp-packet-mtu
   - CLI: --udp-ephemeral-mtu -> --udp-ephemeral-packet-mtu
   - CLI: --tls-mtu -> --tls-max-record-bytes (keep TLS record terminology)
   - Update any doc examples and config snippets to match the new names.
2) Rename transport MTU properties to packet units:
   - Transport.send_mtu/recv_mtu -> send_packet_mtu/recv_packet_mtu.
   - Update all transports to expose packet MTUs with the new names.
   - Update call sites to use the new properties.
3) Rename tunnel MTU state to packet units only:
   - BaseTunnel _send_mtu/_recv_mtu/_default_mtu ->
     _send_packet_mtu/_recv_packet_mtu/_default_packet_mtu.
   - Proposed/pending MTU fields use packet units as well.
   - Accessors return packet_mtu; payload bytes are derived locally when
     building segments or validating sizes.
   - Update Alice/Bob tunnel code to compute payload bytes via
     (packet_mtu - PACKET_HEADER_SIZE) on use.
4) Keep MTU control messages consistent with payload bytes:
   - Keep on-wire tun_mtu fields as payload bytes for protocol stability.
   - Add explicit conversions at boundaries (add/subtract
     PACKET_HEADER_SIZE) without storing payload_mtu fields.
   - Rename locals/helpers to payload_bytes or packet_mtu to clarify units.
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
