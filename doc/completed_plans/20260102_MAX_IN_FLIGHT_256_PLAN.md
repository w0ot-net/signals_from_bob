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
- Transport pending caps are tied to max_in_flight, which is still capped at 64.
- SACK_MAX mask is 64-bit, so high SACK bits would be truncated after expansion.
- SACK wire order for the expanded bitmap is not explicit, risking a reversed
  mapping across the 64-bit word splits.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in doc/ASYMMETRY.md.
- Keepalive pongs are suppressed when any channel has pending data.
- MTU negotiation remains asymmetric (independent send/recv MTUs).
- Breaking protocol change is acceptable, but update all call sites and docs.
- Do not run E2E tests under tests/e2e/ (DNS direct mode uses port 5353; authoritative uses port 53).
- Historical/troubleshooting docs are out of scope for this change.

## Affected Components
- sfb/protocol/constants.py (SACK_BITS/SACK_SIZE/SACK_MAX/PACKET_HEADER_SIZE/MAX_IN_FLIGHT offsets)
- sfb/protocol/__init__.py (remove DEFAULT_MAX_IN_FLIGHT export)
- sfb/protocol/packet.py (PacketHeader encoding/decoding, sack helpers, repr)
- sfb/protocol/segment.py (max payload size derives from header size)
- sfb/reliability/send_window.py (require explicit max_in_flight, SACK scan)
- sfb/reliability/recv_window.py (SACK window checks, max buffer cap)
- sfb/tunnel/base_tunnel.py (MAX_WINDOW, MTU math using PACKET_HEADER_SIZE)
- sfb/tunnel/alice_tunnel.py (window growth clamps against MAX_WINDOW)
- sfb/tunnel/bob_tunnel.py (response payload cap uses PACKET_HEADER_SIZE)
- sfb/config.py (max_in_flight defaults/validation, pacing bounds, initial window,
  transport pending defaults)
- sfb/transport/dns/dns_client.py (inflight cap tied to tunnel_max_in_flight)
- sfb/transport/icmp/icmp_client.py (inflight cap tied to tunnel_max_in_flight)
- sfb/transport/memory/memory_client.py (inflight cap default matches tunnel_max_in_flight)
- sfb/modules/socks/data_pump.py (outbound buffer cap scales with max_in_flight)
- tests/test_packet.py, tests/test_reliability.py, tests/test_tunnel.py
- doc/PROTOCOL.md, doc/RELIABILITY.md, doc/CONTROL_MESSAGES.md, doc/TUNNEL.md
- doc/TRANSPORTS.md, doc/ICMP_TRANSPORT.md, doc/ASYMMETRY.md, doc/DNS_TRANSPORT.md

## Plan
1. Update protocol constants and header layout.
   - Set SACK_BITS and MAX_IN_FLIGHT to 256; set SACK_SIZE to 32; update
     PACKET_HEADER_SIZE and offsets.
   - Update SACK_MAX to cover 256 bits and keep masking consistent.
   - Raise max_in_flight default to 256 and remove DEFAULT_MAX_IN_FLIGHT.
2. Extend PacketHeader encoding/decoding for a 256-bit SACK.
   - Encode SACK as four big-endian u64 words (or 32 bytes) while keeping sack
     as an integer in memory.
   - Define wire order explicitly: bit 0 is ack+1 and bit 255 is ack+256; pack
     the 256-bit integer as big-endian bytes so the highest-order bit maps to
     offset 256.
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
   - Update config validation ranges for max_in_flight,
     tunnel_initial_window, and pacing min/max inflight bounds to 1-256.
   - Keep transport pending caps tied to max_in_flight so transports do not
     introduce separate caps.
   - Ensure transport defaults (e.g., memory) use the configured max_in_flight.
5. MTU and payload sizing.
   - Ensure PACKET_HEADER_SIZE changes propagate to MTU math, payload caps, and
     segment max payload.
   - Keep protocol_initial_mtu semantics as payload bytes; update docs that
     described it as a total packet size limit and note the header is added
     on the wire.
   - Re-evaluate default MTU safety with the larger header and confirm payload
     + header stays within pre-negotiation limits.
6. Documentation updates.
   - Update protocol header diagrams, SACK description, and max_in_flight caps
     to 256.
   - Fix Reliability doc to describe SACK size correctly (currently says
     16-bit).
   - Update protocol MTU wording to describe payload bytes vs header bytes and
     the pre-negotiation payload limit.
   - Note the wire-format change and requirement that both sides upgrade
     together.
   - Limit doc changes to core protocol/tunnel docs; leave historical/troubleshooting
     docs unchanged.
7. Tests and validation.
   - Update unit tests for new header size and 256-bit SACK encode/decode.
   - Add tests for SACK bit set/clear at boundary offsets (64/65, 128/129,
     192/193, 255/256) to confirm wire order across word splits.
   - Add coverage to ensure SACK_MAX masks all 256 bits (no truncation).
   - Update any max_in_flight cap tests to 256.
   - Update SendWindow usage to pass explicit max_in_flight where defaults existed.
   - Run only unit tests: python3 -m unittest tests.test_packet tests.test_reliability tests.test_tunnel

## Acceptance Criteria
- MAX_IN_FLIGHT and SACK_BITS are 256, header size and offsets are consistent.
- SACK_MAX masks the full 256-bit window without truncation.
- PacketHeader encodes/decodes 256-bit SACK correctly on Python 2 and 3.
- SACK wire order is documented and tests cover boundary offsets across splits.
- Window negotiation and config accept 1-256 and honor max_in_flight only.
- SendWindow has no implicit max_in_flight default; all call sites pass it explicitly.
- Docs reflect 256-bit SACK and new header size in core protocol/tunnel docs.
- Unit tests pass (no E2E tests run).

## Execution Notes
- Updated protocol constants, header encoding/decoding, window caps, and config ranges to 256.
- Updated reliability window logic, MTU/header sizing docs, and protocol/window negotiation docs.
- Added unit tests for 256-bit SACK wire order boundaries and masking.
- Ran: python3 -m unittest tests.test_packet tests.test_reliability tests.test_tunnel
