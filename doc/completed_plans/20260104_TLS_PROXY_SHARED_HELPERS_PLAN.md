# TLS Proxy Shared Helpers Plan

## Summary
Factor the HTTP CONNECT request/response handling and proxy configuration
validation into shared helpers so both `tls_handshake` and the future
`tls_handshake_squid` transport can reuse them without subclassing.

## Goals
- Extract HTTP CONNECT request build/parse and timeout handling into a shared
  module with clear, testable functions.
- Extract proxy config validation (host/port/auth/timeout) into a shared
  helper used by multiple transports.
- Preserve Python 2.7/3 compatibility and standard-library-only constraints.
- Keep existing behavior unchanged for `tls_handshake`.

## Non-Goals
- Implement `tls_handshake_squid` itself.
- Change proxy semantics or add new authentication schemes.

## Affected Components
- `sfb/transport/tls_handshake/tls_handshake_client.py`
- `sfb/transport/tls_handshake/tls_handshake_config.py`
- `sfb/transport/tls_handshake_squid/` (future transport will consume helpers)
- `sfb/transport/proxy_helpers.py` (new shared module)
- `tests/test_tls_proxy_helpers.py`
- `tests/test_tls_client_server.py`

## Plan
1) Create shared proxy helper module.
   - Add `sfb/transport/proxy_helpers.py` with:
     - `build_connect_request(target_hostport, proxy_auth=None)`
     - `parse_connect_response(buffer)` returning status + header_end
     - `validate_proxy_config(tls_http_proxy, tls_http_proxy_auth,
       tls_proxy_timeout, connect_timeout)` returning normalized values.
   - Keep ASCII-only inputs and preserve current error messages where possible.

2) Move proxy config validation from `tls_handshake_config.py`.
   - Replace inline proxy validation with `validate_proxy_config`.
   - Preserve current defaults and error conditions.

3) Update `tls_handshake_client.py` to use the shared helpers.
   - Replace `_build_proxy_request` and `_parse_proxy_status` logic with calls
     to `build_connect_request` and `parse_connect_response`.
   - Keep the existing state machine and timeout behavior unchanged.

4) Add targeted unit tests for the helpers.
   - Cover CONNECT request formatting (with and without auth).
   - Cover response parsing for valid status, invalid status, and oversized
     headers.
   - Cover proxy config validation errors and normalized timeouts.

5) Touch up existing TLS client/server tests as needed.
   - Only update tests if helper extraction changes error text or behavior.

## Validation
- Run `python3 -m unittest tests.test_tls_proxy_helpers`.
- Run `python3 -m unittest tests.test_tls_client_server`.
- Do not run tests/e2e (user-run only).

## Execution Notes
- Implemented shared proxy helpers and updated TLS handshake client/config.
- Added unit tests for proxy helpers.
- Ran `python3 -m unittest tests.test_tls_proxy_helpers`.
- Ran `python3 -m unittest tests.test_tls_client_server` (skipped 5).
