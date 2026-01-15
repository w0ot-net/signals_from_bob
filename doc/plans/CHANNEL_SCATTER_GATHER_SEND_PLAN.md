# Channel Scatter/Gather Send Plan

Status: draft

## Summary
Switch the channel send path to return chunk lists plus total length, then pack
segments using scatter/gather assembly to avoid per-segment `b''.join` copies.

## Goals
- Reduce allocations/copies in the send hot path while preserving semantics.
- Keep lock ordering and channel manager packing behavior intact.
- Preserve Python 2.7/3 compatibility and Windows/Linux support.

## Non-Goals
- Change protocol wire format or message semantics.
- Modify transport implementations or encryption algorithms.
- Add or run automated tests.

## Affected Components
- `sfb/channel/channel.py`
- `sfb/channel/control_channel.py`
- `sfb/channel/channel_manager.py`
- `sfb/protocol/segment.py`
- `sfb/protocol/__init__.py`
- `sfb/tunnel/base_tunnel.py`
- `doc/architecture/CHANNEL_MANAGER.md`

## Plan
1. Define a chunked payload representation and invariants.
   - Choose a return type for `_take_send_data`, e.g. `(chunks, total_len)`.
   - Invariants: `chunks` is a non-empty list for `total_len > 0`, each chunk is
     bytes-like with itemsize 1, and `sum(len(chunk)) == total_len`.
   - Enforce invariants and fail fast on violations; no fallback conversions.
   - Use explicit loops (no comprehensions) to keep Python 2 minified builds safe.

2. Update channel send extraction to return chunks.
   - Change `Channel._take_send_data` to return `(chunks, total_len)` and avoid
     `b''.join` on multi-chunk paths.
   - Keep the max-size slicing behavior and send buffer accounting intact.
   - Update `ControlChannel._take_send_data` and its send-event clearing logic.
   - Treat this as a breaking internal change and update all call sites.

3. Update segment collection to accept chunked payloads.
   - Adjust `ChannelManager._take_segment` to handle `(chunks, total_len)`.
   - Create `Segment` objects with chunked payloads and enforce length <= 0xFFFF.
   - Keep round-robin selection and payload size checks unchanged.

4. Extend segment/packet encoding to assemble parts efficiently.
   - Teach `Segment` to store `data_parts` plus `data_len` (or equivalent).
   - Add `Segment.encode_parts()` that returns `[header] + parts` without joining.
   - Update `Packet.encode` and `BaseTunnel._encode_segments` to build a parts
     list and join once at the final encode/encrypt boundary.
   - Update `pack_segments` to use length tracking + parts lists (no `packed +=`).

5. Update documentation.
   - Document the chunked segment assembly and invariants in
     `doc/architecture/CHANNEL_MANAGER.md`.

## Testing
- Do not run tests.
