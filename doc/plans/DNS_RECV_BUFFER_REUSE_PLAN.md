# DNS Receive Buffer Reuse Plan

Status: draft

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

## Plan
1. Reuse a receive buffer in `sfb/transport/dns/dns_client.py`.
   - Allocate `bytearray(self._recv_bufsize)` once.
   - Use `recvfrom_into` and pass a memoryview slice of the received bytes to
     `_parse_response`.
   - When `_recv_bufsize` changes, refresh the buffer to the new size.
2. Accept bytes-like views in DNS name parsing in `sfb/transport/dns/dns_codec.py`.
   - Replace `to_bytes` coercion in `decode_name` and `skip_name` with a helper
     that returns a bytes-like view (memoryview on Python 3, buffer on Python 2)
     without copying.
   - Convert only the per-label slice to bytes for `.decode('ascii')`.
3. Update call sites that parse DNS packets.
   - Ensure `dns_client._parse_response` and `dns_server._parse_query` work with
     bytes-like views and struct.unpack slices.
   - Keep validation and error handling identical to the current behavior.
4. Manual verification.
   - Confirm the receive buffer is reused across packets.
   - Confirm only small label slices are copied when decoding names.

## Testing
- Do not run tests.
