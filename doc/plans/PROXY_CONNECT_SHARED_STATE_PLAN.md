# Proxy Connect Shared State Plan

Status: draft

## Summary

Introduce a shared HTTP CONNECT proxy handshake state helper so transports can
reuse proxy read/write/timeout logic without duplicating socket handling or
logging.

## Goals

- Provide a small, reusable proxy handshake state machine for client transports.
- Centralize proxy I/O and logging while preserving current behavior.
- Preserve current proxy wire behavior, timeouts, and error logging semantics.
- Keep Python 2.7/3 compatibility and Windows/Linux support.
- Limit helper API surface to standard library usage.

## Non-Goals

- Changing HTTP CONNECT wire format or authentication behavior.
- Adding new configuration options or defaults.
- Refactoring non-proxy transport logic beyond necessary call-site changes.
- Running E2E tests (user-owned).

## Affected Components

- sfb/transport/proxy_helpers.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- tests/test_tls_client_server.py
- tests/test_tls_handshake_bump_client_server.py

## Plan

1. Define a shared proxy state helper
   - Add a small state object (for example, ProxyConnectState) that owns
     send/recv buffers, scan offset, and proxy deadline.
   - Expose `wants_read()` / `wants_write()` and `deadline()` helpers.
   - Provide `on_read(sock)` and `on_write(sock)` that perform I/O, advance
     state, and return explicit status codes (incomplete/complete/error/too_large).
   - Inject per-transport behavior: errno extraction, temporary error list, and
     log settings (event name/message prefix) so error handling matches current
     Windows/Linux behavior and log output.
   - Preserve the bump-client scan offset rule (`len(buf) - 3`) inside the helper.
   - Keep parse/limit logic in `proxy_helpers.py` for consistent behavior.

2. Integrate with TLS handshake client
   - Replace per-connection proxy fields with the shared proxy state helper.
   - Use the helper for interest selection and deadline pruning.
   - Pass TLS client log settings and errno/temp-error handling into the helper
     so `tls.proxy_error` events and `TransportError` behavior remain unchanged.

3. Integrate with TLS handshake bump client
   - Replace per-connection proxy fields and proxy scan handling with the
     shared helper.
   - Pass TLS bump log settings and errno/temp-error handling into the helper
     so `tls_bump.proxy_error` events and `TransportError` behavior remain unchanged.
   - Keep existing proxy timeout handling and transition to the TLS handshake
     phase on successful CONNECT.

4. Update tests only if needed
   - Adjust any direct `_PendingConn` construction for new proxy state fields.
   - Add focused unit tests for the proxy helper if existing coverage is
     insufficient.

## Validation

- Run `python3 -m unittest tests.test_tls_client_server`.
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Do not run tests in `tests/e2e/`.
