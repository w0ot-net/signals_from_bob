# Proxy Connect Shared State Plan

Status: draft

## Summary

Introduce a shared HTTP CONNECT proxy handshake helper that fully owns the
proxy-phase state and I/O so transports only keep a single proxy field. This is
an internal breaking refactor aimed at higher readability, lower duplication,
and no performance regressions.

## Goals

- Provide a small, reusable proxy handshake helper for client transports.
- Centralize proxy I/O, logging, and state transitions to remove duplication.
- Preserve current proxy wire behavior, timeouts, and log event schemas.
- Keep Python 2.7/3 compatibility and Windows/Linux support.
- Limit helper API surface to standard library usage.
- Avoid extra copies in proxy send/recv paths.

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

1. Define a shared proxy handshake helper
   - Add a helper class (for example, `ProxyConnect`) that owns proxy buffers,
     offsets, scan offset, deadline, and a reference to the socket.
   - Expose `wants_read()`, `wants_write()`, `deadline()`, and
     `drive(can_read, can_write, now)` returning explicit status values
     (`in_progress`, `done`, `closed`).
   - Preserve behavior: socket errors raise `TransportError`, EOF/parse/oversize
     paths only log and return `closed`.
   - Inject per-transport behavior: errno extraction, temporary error list, and
     a log callback so Windows/Linux handling and event schemas stay identical.
   - Preserve the bump-client scan offset rule (`len(buf) - 3`) inside the helper.
   - Keep parse/limit logic in `proxy_helpers.py` and use `buffer_view` to avoid
     extra copies.

2. Integrate with TLS handshake client (breaking internal refactor)
   - Replace per-connection proxy fields with a single `proxy_state` helper.
   - Use the helper for phase interests and deadline pruning.
   - In `_drive_read/_drive_write`, call `proxy_state.drive(...)` and handle
     status to transition to `_PHASE_REQUEST` or close pending sockets.
   - Remove `_flush_proxy_send` and `_recv_proxy_response` in favor of the helper.

3. Integrate with TLS handshake bump client (breaking internal refactor)
   - Replace per-connection proxy fields and scan handling with `proxy_state`.
   - Use the helper for proxy interests and deadlines.
   - On `done`, transition into `_start_handshake`; on `closed`, close pending.
   - Remove `_flush_proxy_send` and `_recv_proxy_response` in favor of the helper.

4. Consolidate CONNECT request building
   - Remove `_build_connect_request` from the bump client.
   - Extend `build_connect_request` in `proxy_helpers.py` to accept optional
     label strings so bump-specific error messages remain clear.

5. Update tests only if needed
   - Adjust any direct `_PendingConn` construction for the new proxy state field.
   - Add focused unit tests for the proxy helper if existing coverage is
     insufficient.

## Validation

- Run `python3 -m unittest tests.test_tls_client_server`.
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Do not run tests in `tests/e2e/`.
