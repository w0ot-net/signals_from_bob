# DNS Connected UDP Plan

Status: draft

## Summary
Switch the DNS client to a connected UDP socket to reduce per-send overhead and
filter responses to the configured resolver.

## Goals
- Use `socket.connect` and `send`/`recv` in the DNS client hot path.
- Preserve current protocol behavior and logging.
- Keep the change contained to the DNS client.

## Non-Goals
- Change resolver selection logic.
- Modify server-side code.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_client.py`

## Plan
1. Connect the UDP socket on initialization.
   - After creating the socket, call `self._sock.connect(self._resolver)`.
   - Keep the resolver tuple for logging.
2. Update send/recv to use the connected socket.
   - Replace `sendto(query_pkt, self._resolver)` with `send(query_pkt)`.
   - Replace `recvfrom(self._recv_bufsize)` with `recv(self._recv_bufsize)` and
     adjust logging to omit the source address.
3. Preserve error handling.
   - Keep `TransportError` wrapping on send/receive failures.
   - Ensure `select` usage remains unchanged.
4. Manual verification.
   - Confirm the socket is connected once and reused.
   - Confirm the client still handles stale responses and pending cleanup as
     before.

## Testing
- Do not run tests.
