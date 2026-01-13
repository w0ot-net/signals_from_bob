# ICMP Payload Memoryview Plan

Status: draft

## Summary
Reduce per-packet CPU overhead in ICMP parsing by keeping payloads as
memoryview/bytes-like objects until the tunnel decode boundary, avoiding an
unnecessary copy on every packet.

## Goals
- Avoid per-packet payload copies in ICMP receive parsing.
- Preserve Python 2.7/3 compatibility and existing ICMP wire behavior.
- Keep caller behavior unchanged at the tunnel decode boundary.

## Non-Goals
- Change ICMP checksum validation or packet format.
- Modify transport retry or pacing behavior.
- Add or run automated tests.

## Affected Components
- `sfb/transport/icmp/icmp_packet.py`
- `sfb/transport/icmp/icmp_client.py`

## Plan
1. Make `parse_icmp_echo` return bytes-like payloads without copying.
   - In `sfb/transport/icmp/icmp_packet.py`, return the payload slice directly
     instead of forcing `to_bytes`.
   - Ensure the returned payload remains a bytes-like object on both Python 2
     and 3 (e.g., bytes or memoryview).

2. Defer payload materialization to the tunnel decode boundary.
   - In `sfb/transport/icmp/icmp_client.py`, pass through the payload without
     converting it until the caller that needs immutable bytes.
   - Audit the receive path to identify where a `bytes` object is required and
     convert once at that boundary.

3. Verify no regressions in payload size checks.
   - Keep the existing payload length checks intact, using `len(payload)` on the
     bytes-like object.

## Testing
- Do not run tests.
