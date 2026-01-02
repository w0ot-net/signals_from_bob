# Max In-Flight 256 Plan

## Goal
Increase the negotiated max_in_flight cap from 64 to 256 by expanding the SACK
bitmap and header layout while keeping reliability semantics unchanged.

## Issues
- The SACK bitmap is fixed at 64 bits and encoded as 8 bytes in the header, so
  max_in_flight is capped at 64.
- Config validation, tunnel negotiation, and documentation hardcode the 64 cap.
- Packet header size (14 bytes) and MTU calculations assume a 64-bit SACK.
- Tests and troubleshooting docs assume max_in_flight=64.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in doc/ASYMMETRY.md.
- Keepalive pongs are suppressed when any channel has pending data.
- MTU negotiation remains asymmetric (independent send/recv MTUs).
- Breaking protocol change is acceptable, but update all call sites and docs.
- Do not run E2E tests under tests/e2e/ (DNS direct mode uses port 5353; authoritative uses port 53).

## Affected Components
- sfb/protocol/constants.py (SACK_BITS/SACK_SIZE/PACKET_HEADER_SIZE/MAX_IN_FLIGHT offsets)
- sfb/protocol/packet.py (PacketHeader encoding/decoding, sack helpers, repr)
- sfb/protocol/segment.py (max payload size derives from header size)
- sfb/reliability/send_window.py (SACK scan and max_in_flight validation)
- sfb/reliability/recv_window.py (SACK window checks, max buffer cap)
- sfb/tunnel/base_tunnel.py (MAX_WINDOW, MTU math using PACKET_HEADER_SIZE)
- sfb/config.py (tunnel_max_in_flight defaults/validation, pacing bounds, initial window)
- tests/test_packet.py, tests/test_reliability.py, tests/test_tunnel.py
- doc/PROTOCOL.md, doc/RELIABILITY.md, doc/CONTROL_MESSAGES.md, doc/TUNNEL.md
- doc/TRANSPORTS.md, doc/ICMP_TRANSPORT.md, doc/ASYMMETRY.md
- doc/troubleshooting_socks_channel_starvation.md, doc/bugs/scp_stalled_icmp_socks.md

## Plan
1. Update protocol constants and header layout.
   - Set SACK_BITS and MAX_IN_FLIGHT to 256; set SACK_SIZE to 32; update
     PACKET_HEADER_SIZE and offsets.
   - Decide whether to raise tunnel_max_in_flight default to 256 (recommended)
     and keep DEFAULT_MAX_IN_FLIGHT at 8.
2. Extend PacketHeader encoding/decoding for a 256-bit SACK.
   - Encode SACK as four big-endian u64 words (or 32 bytes) while keeping sack
     as an integer in memory.
   - Implement Python 2/3 compatible pack/unpack (manual split/combine, no
     int.to_bytes).
   - Update __repr__ to print full-width hex (64 digits) and docstrings to
     reflect 256-bit SACK and the new header size.
3. Reliability window updates.
   - Allow SendWindow/RecvWindow validation to accept max_in_flight up to 256.
   - Update SACK scanning loops to cover 256 bits and keep drop rules for
     out-of-window packets consistent.
   - Confirm retransmit behavior and SACK coverage semantics remain unchanged.
4. Tunnel negotiation and config adjustments.
   - Raise BaseTunnel.MAX_WINDOW to 256.
   - Update config validation ranges for tunnel_max_in_flight,
     tunnel_initial_window, and pacing min/max inflight bounds to 1-256.
   - Consider bumping dns_max_pending/icmp_max_pending defaults to align with
     the larger window, or document transport cap as the limiting factor.
5. MTU and payload sizing.
   - Ensure PACKET_HEADER_SIZE changes propagate to MTU math, payload caps, and
     segment max payload.
   - Re-evaluate protocol_initial_mtu and default MTU safety with the larger
     header.
6. Documentation updates.
   - Update protocol header diagrams, SACK description, and max_in_flight caps
     to 256.
   - Fix Reliability doc to describe SACK size correctly (currently says
     16-bit).
   - Note the wire-format change and requirement that both sides upgrade
     together.
7. Tests and validation.
   - Update unit tests for new header size and 256-bit SACK encode/decode.
   - Add tests for SACK bit set/clear at high offsets (e.g., 128, 256).
   - Update any max_in_flight cap tests to 256.
   - Run only unit tests: python3 -m unittest tests.test_packet tests.test_reliability tests.test_tunnel

## Acceptance Criteria
- MAX_IN_FLIGHT and SACK_BITS are 256, header size and offsets are consistent.
- PacketHeader encodes/decodes 256-bit SACK correctly on Python 2 and 3.
- Window negotiation and config accept 1-256 and honor transport caps.
- Docs reflect 256-bit SACK and new header size; no stale 64 cap text remains.
- Unit tests pass (no E2E tests run).
