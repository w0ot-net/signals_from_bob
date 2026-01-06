# MTU Clarity and Auto Selection Plan

## Goal
- Document the minimum/maximum MTU for every transport with consistent terms.
- Auto-select the largest safe MTU per transport without user tuning.
- Keep MTU knobs as advanced overrides (default safe caps like 1350 remain enforceable).

## Non-Goals
- Implement path MTU discovery or runtime probing.
- Change asymmetric MTU negotiation, retransmit logic, or keepalive behavior.
- Add non-stdlib dependencies or drop Python 2.7/3 compatibility.
- Run E2E tests under tests/e2e (user will run them).

## Affected Components
- sfb/config.py
- sfb/cli.py
- sfb/transport/mtu_limits.py (new)
- sfb/transport/icmp/icmp_client.py
- sfb/transport/icmp/icmp_server.py
- sfb/transport/udp_ephemeral/udp_ephemeral_client.py
- sfb/transport/udp_ephemeral/udp_ephemeral_server.py
- sfb/transport/dns/dns_client.py
- sfb/transport/dns/dns_server.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake/tls_handshake_server.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_server.py
- doc/TRANSPORTS.md
- doc/DNS_TRANSPORT.md
- doc/ICMP_TRANSPORT.md
- doc/UDP_EPHEMERAL_TRANSPORT.md
- doc/TLS_TRANSPORT.md
- doc/TUNNEL.md
- doc/PROTOCOL.md

## Plan
1) Define MTU terminology in docs:
   - Packet MTU (packet_mtu) = max packet bytes on the wire (header + segments).
   - Payload bytes = packet_mtu - PACKET_HEADER_SIZE (segment bytes).
   - Minimum packet MTU = PACKET_HEADER_SIZE + 1 (at least 1 byte of payload).
   - Reaffirm per-direction (asymmetric) negotiation.
   - Explicitly map transport.send_packet_mtu/recv_packet_mtu (packet bytes)
     to tunnel payload bytes in BaseTunnel and call out that tun_mtu values
     are payload bytes, not full packet bytes.
2) Add per-transport MTU limit tables:
   - DNS: show query/response max packet sizes as functions of base_domain,
     label_max_len, cname_label, and edns_size; explain that CNAME+512 has a
     per-query payload cap based on QNAME length.
   - ICMP/UDP: document the safe default cap (1350) and that larger values
     increase fragmentation risk on the public Internet.
   - TLS ClientHello: document record-size caps and computed payload sizes.
   - TLS bump: document SNI/CN payload caps and the ClientHello record-size
     cap (tls_bump_max_clienthello_bytes) that bounds Alice->Bob MTU.
3) Implement shared MTU resolution in sfb/transport/mtu_limits.py:
   - Provide a function that returns send_packet_mtu/recv_packet_mtu (packet
     bytes), min_packet_mtu, and a dict of constraint details for logging.
   - DNS/TLS/TLS bump use existing codec math; ICMP/UDP clamp to
     min(protocol_max_packet_mtu, configured_cap).
   - TLS bump send_packet_mtu clamps to
     min(sni_payload_cap, clienthello_record_cap), where
     clienthello_record_cap is derived from tls_bump_max_clienthello_bytes.
   - DNS helper returns base query/response packet MTUs; per-query CNAME
     payload caps remain request-specific.
   - Keep asymmetric results where the transport supports it (DNS, TLS, bump).
4) Update transports to use the shared MTU resolver:
   - Replace per-transport MTU calculations with the helper where possible.
   - DNS server keeps _response_payload_cap and continues to set
     responder.payload_cap to the per-query cap (optionally min'd with the
     base MTU).
   - Log a single transport.mtu_limits event at init with computed values and
     constraint inputs (base_domain length, edns_size, caps, tls_bump_max_clienthello_bytes).
5) Tighten validation and errors:
   - Fail fast if computed send_packet_mtu/recv_packet_mtu < PACKET_HEADER_SIZE + 1, with
     transport-specific error messages (e.g., DNS base_domain too long, TLS bump
     max ClientHello bytes too small).
   - Keep existing DNS/TLS validation and surface clearer MTU-related errors.
6) Default-safe caps and override behavior:
   - Treat icmp_packet_mtu and udp_ephemeral_packet_mtu as caps; defaults
     remain 1350 (safe on typical 1500 MTU links).
   - Auto selection always clamps to these caps, even if a larger size is
     otherwise possible.
   - Document these as advanced overrides; defaults should be "optimal" for
     Internet paths without user tuning.
   - CLI help text should label MTU flags as advanced overrides and discourage
     routine tuning.
7) Update CLI help and summary docs:
   - CLI help should say "advanced override; leave default for auto".
   - TRANSPORTS/README summarize per-transport MTU selection and defaults.
   - TLS_TRANSPORT/PROTOCOL/TUNNEL explicitly distinguish transport packet MTU
     from tunnel payload MTU and align negotiation wording.
   - Do not add new runtime dependencies or change E2E test instructions.
