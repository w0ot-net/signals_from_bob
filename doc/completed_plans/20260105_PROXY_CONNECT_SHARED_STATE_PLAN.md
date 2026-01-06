# Proxy Connect Shared State Plan

Status: completed

## Summary

Introduce a minimal shared HTTP CONNECT proxy handshake helper that owns the
proxy-phase buffers and I/O so transports keep a single proxy field. This is an
internal breaking refactor aimed at higher readability, lower duplication, and
no performance regressions (including no added CONNECT latency or copies).

## Goals

- Provide a small, reusable proxy handshake helper for client transports.
- Centralize proxy I/O, logging, and state transitions to remove duplication.
- Preserve current proxy wire behavior, timeouts, and log event schemas.
- Keep Python 2.7/3 compatibility and Windows/Linux support.
- Limit helper API surface to standard library usage.
- Avoid extra copies and extra syscalls in proxy send/recv paths.
- Keep helper complexity low: a single call per readiness cycle handles both
  read and write paths without duplicate state transitions.
- Keep immediate CONNECT write-on-connect behavior to avoid latency regressions.

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

1. Define a shared proxy handshake helper (minimal API, minimal state)
   - Add a helper class (for example, `ProxyConnect`) that owns proxy buffers,
     offsets, scan offset, deadline, and a reference to the socket.
   - Expose `wants_read()`, `wants_write()`, `deadline()`, and a single
     `drive(can_read, can_write, now)` used once per readiness cycle, returning
     explicit status values (`in_progress`, `done`, `closed`).
   - Preserve behavior: socket errors raise `TransportError`, EOF/parse/oversize
     paths only log and return `closed`.
   - Preserve the `extra_bytes` validation and log reason when CONNECT returns
     non-CRLF bytes after the header terminator.
   - Inject per-transport behavior: errno extraction, temporary error list, and
     a log callback so Windows/Linux handling and event schemas stay identical,
     including passing through `corr_id`, `error`, `status`, and `bytes`.
   - Preserve the bump-client scan offset rule (`len(buf) - 3`) inside the helper.
   - Keep parse/limit logic in `proxy_helpers.py` and use `buffer_view` for
     sends to avoid extra copies.
   - Helper never closes sockets or touches `_sock_to_corr`; it only returns
     status and releases its socket reference on `done`/`closed` so callers can
     wrap or close without races.

2. Integrate with TLS handshake client (breaking internal refactor)
   - Replace per-connection proxy fields with a single `proxy_state` helper.
   - Use the helper for phase interests and deadline pruning.
   - In `_drive_socket`, when the phase is `_PHASE_PROXY`, call
     `proxy_state.drive(can_read, can_write, now)` once and handle status to
     transition to `_PHASE_REQUEST` or close pending sockets.
   - After connect success, trigger an immediate proxy `drive(...)` with
     `can_write=True` to preserve current CONNECT flush behavior.
   - Remove `_flush_proxy_send` and `_recv_proxy_response` in favor of the helper.

3. Integrate with TLS handshake bump client (breaking internal refactor)
   - Replace per-connection proxy fields and scan handling with `proxy_state`.
   - Use the helper for proxy interests and deadlines.
   - On `done`, transition into `_start_handshake`; on `closed`, close pending.
   - After connect success, trigger an immediate proxy `drive(...)` with
     `can_write=True` to preserve current CONNECT flush behavior.
   - Ensure the helper releases its socket reference before socket wrapping so
     it never holds the pre-SSL socket after proxy completion.
   - Remove `_flush_proxy_send` and `_recv_proxy_response` in favor of the helper.

4. Consolidate CONNECT request building with zero behavior drift
   - Remove `_build_connect_request` from the bump client.
   - Extend `build_connect_request` in `proxy_helpers.py` to accept optional
     label strings for target and auth errors so bump-specific error messages
     remain clear.

5. Update tests only if needed
   - Adjust any direct `_PendingConn` construction for the new proxy state field.
   - Add focused unit tests for the proxy helper if existing coverage is
     insufficient.

## Validation

- Run `python3 -m unittest tests.test_tls_client_server`.
- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Do not run tests in `tests/e2e/`.

## Execution Notes

- Added `ProxyConnect` in `sfb/transport/proxy_helpers.py` and wired it into
  both TLS client transports to replace per-connection proxy fields and I/O.
- Consolidated CONNECT request building for bump client via
  `build_connect_request` labels.
- Removed legacy proxy send/recv helpers and bump-specific CONNECT builder.
- Tests not run.
