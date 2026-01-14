# ICMP Payload Memoryview Plan

Status: draft

## Summary
Reduce per-packet CPU overhead by avoiding ICMP payload copies in the receive
path. Return payload views (memoryview on Python 3; bytes on Python 2), update
ICMP client/server call sites plus the transport data contract to accept
bytes-like objects, and leave packet/segment decode unchanged unless profiling
proves slicing is a measurable bottleneck.

## Goals
- Avoid per-packet payload copies in ICMP receive parsing.
- Allow transport payloads to be bytes-like objects end-to-end.
- Preserve Python 2.7/3 compatibility and existing ICMP/tunnel wire behavior.

## Non-Goals
- Change ICMP checksum validation or packet format.
- Modify transport retry or pacing behavior.
- Rework packet/segment decoding (offset-based) without profiling evidence.
- Add or run automated tests.

## Affected Components
- `sfb/transport/icmp/icmp_packet.py`
- `sfb/transport/icmp/icmp_client.py`
- `sfb/transport/icmp/icmp_server.py`
- `sfb/transport/transport_base.py`

## Plan
1. Return payload views from `parse_icmp_echo`.
   - In `sfb/transport/icmp/icmp_packet.py`, return a payload view
     (memoryview on Python 3; bytes on Python 2) instead of `to_bytes`.
   - Document that the view aliases the receive buffer on Python 3.

2. Update ICMP client/server receive paths to accept bytes-like payloads.
   - In `sfb/transport/icmp/icmp_client.py` and
     `sfb/transport/icmp/icmp_server.py`, keep MTU checks intact using
     `len(payload)` and pass payloads through without copying.

3. Update the transport data contract to accept bytes-like objects.
   - In `sfb/transport/transport_base.py`, clarify that transport `recv()`
     returns bytes-like data and that higher layers must accept it.

## Testing
- Do not run tests.
