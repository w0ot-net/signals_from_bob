# ICMP Payload Memoryview Plan

Status: draft

## Summary
Reduce per-packet CPU overhead by keeping ICMP payloads as bytes-like views and
pushing zero-copy parsing through tunnel/protocol decode using offset-based
unpackers. This is a breaking change to parsing/decode contracts so callers
must consume views/offsets instead of raw byte slices.

## Goals
- Avoid per-packet payload copies in ICMP receive parsing.
- Avoid packet header and segment slice copies during decode by using
  offset-based unpackers.
- Preserve Python 2.7/3 compatibility and existing ICMP/tunnel wire behavior.
- Keep copies confined to crypto/segment materialization boundaries only.

## Non-Goals
- Change ICMP checksum validation or packet format.
- Modify transport retry or pacing behavior.
- Add or run automated tests.

## Affected Components
- `sfb/transport/icmp/icmp_packet.py`
- `sfb/transport/icmp/icmp_client.py`
- `sfb/transport/icmp/icmp_server.py`
- `sfb/tunnel/base_tunnel.py`
- `sfb/protocol/packet.py`
- `sfb/protocol/segment.py`

## Plan
1. Make `parse_icmp_echo` return view+offset metadata instead of bytes slices.
   - In `sfb/transport/icmp/icmp_packet.py`, return a bytes-like view of the
     ICMP packet plus `payload_offset` and `payload_len` rather than slicing or
     forcing `to_bytes`.
   - Use `struct.unpack_from` with offsets so header parsing does not slice.
   - Ensure the returned view is safe on Python 2/3 (buffer/memoryview) and
     document that it aliases the receive buffer.

2. Update ICMP client/server receive paths to use view+offset payloads.
   - In `sfb/transport/icmp/icmp_client.py` and
     `sfb/transport/icmp/icmp_server.py`, adjust parse results to
     `(icmp_type, ident, seq, packet_view, payload_offset, payload_len)`.
   - Use `payload_len` for MTU checks and pass the view+offset through to the
     tunnel decode boundary without copying.

3. Push offset-based decode through tunnel/protocol parsing.
   - In `sfb/protocol/packet.py`, add `PacketHeader.decode_from(data, offset)`
     using `struct.unpack_from` and avoid `data[:PACKET_HEADER_SIZE]`.
   - In `sfb/protocol/segment.py`, add offset-based decode helpers
     (`Segment.decode_from`, `decode_all_from`) that advance offsets instead of
     slicing.
   - In `sfb/tunnel/base_tunnel.py`, decode headers/segments using offsets and
     views; only materialize bytes at crypto and segment data boundaries where
     required by cipher/segment APIs.

4. Verify no regressions in payload size checks.
   - Keep the existing payload length checks intact, using `payload_len` from
     the parse result.

## Testing
- Do not run tests.
