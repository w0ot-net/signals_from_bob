# DNS Receive Buffer Reuse Plan

Status: completed

## Summary
Reduce per-packet allocations in the DNS client by reusing a UDP receive buffer
and teaching DNS name parsing to accept bytes-like views without copying the
entire packet.

## Goals
- Use recvfrom_into with a preallocated bytearray in the DNS client.
- Avoid full-packet copies in DNS name parsing by operating on bytes-like views.
- Preserve current protocol behavior and error handling.

## Non-Goals
- Change DNS query/response formats or protocol behavior.
- Add new configuration options.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/compat.py`

## Helper Notes
- Use `compat.buffer_view` for zero-copy bytes-like views of packet data.
- Use `compat.byte_at` for single-byte reads from views.
- Use `compat.to_bytes` only for the small label slices that must be decoded.
- Use `compat.bytearray_to_bytes` only at bytes-only boundaries if needed.

## Plan
1. Reuse a receive buffer in `sfb/transport/dns/dns_client.py`.
   - Allocate `bytearray(self._recv_bufsize)` once.
   - Use `recvfrom_into` and pass `compat.buffer_view(recv_buf, length=recv_len)`
     to `_parse_response`.
   - When `_recv_bufsize` changes, refresh the buffer to the new size.
2. Accept bytes-like views in DNS name parsing in `sfb/transport/dns/dns_codec.py`.
   - Replace `to_bytes` coercion in `decode_name` and `skip_name` with
     `compat.buffer_view` to avoid full-packet copies.
   - Use `compat.byte_at` for length/pointer reads from the view.
   - Convert only the per-label slice using `compat.to_bytes` for
     `.decode('ascii')`.
3. Update call sites that parse DNS packets.
   - Ensure `dns_client._parse_response` and `dns_server._parse_query` work with
     bytes-like views and struct.unpack slices.
   - Keep validation and error handling identical to the current behavior.
4. Manual verification.
   - Confirm the receive buffer is reused across packets.
   - Confirm only small label slices are copied when decoding names.

## Testing
- Do not run tests.

## Execution Notes
- Reused a preallocated DNS client receive buffer and refreshed it when the
  configured size changes.
- Switched DNS name parsing to operate on bytes-like views and only copy
  per-label slices for ASCII decoding.
- Tests not run (not requested).
