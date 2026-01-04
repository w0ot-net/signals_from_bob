# TLS Handshake HTTP Proxy Plan

## Context
The TLS ClientHello transport currently connects directly to `tls_target`.
We want to allow Alice to reach Bob through an explicit HTTP proxy by issuing
an HTTP CONNECT and then sending the existing single-record TLS handshake over
the established tunnel.

## Goals
- Add optional HTTP CONNECT proxy support for the TLS ClientHello client.
- Preserve existing behavior when no proxy is configured.
- Keep Python 2.7/3 compatibility, standard library only, and cross-platform.
- Provide clear config/CLI options and logging for proxy failures.

## Non-goals
- Support TLS-intercepting proxies or any proxy that validates TLS handshakes.
- Add SOCKS proxy support to TLS transport (use the SOCKS module instead).
- Implement proxy auto-discovery or environment-variable configuration.
- Implement proxy auth beyond optional Basic credentials.

## Affected components
- sfb/config.py
- sfb/cli.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- doc/TLS_TRANSPORT.md
- tests/test_tls_client_server.py

## Plan
1) Extend configuration and CLI.
   - Add `tls_http_proxy` (host:port) and optional `tls_http_proxy_auth`
     (user:pass) fields to `Config`.
   - Add `--tls-http-proxy` and `--tls-http-proxy-auth` client CLI flags.
   - Validate proxy fields as ASCII and host:port in
     `tls_handshake_config.py`.

2) Parse and store proxy vs target addresses.
   - Keep `target_host` and `target_port` from `tls_target` for CONNECT.
   - Resolve `tls_http_proxy` to IPv4 when configured; keep direct target
     resolution for the no-proxy path.
   - Log a `tls.client_config` field showing proxy settings (host:port only).

3) Add an HTTP CONNECT handshake state machine to the TLS client.
   - Extend `_PendingConn` with proxy send/recv buffers and a proxy deadline.
   - After TCP connect completes, send a CONNECT request to the proxy:
     `CONNECT host:port HTTP/1.1` with `Host:` header and optional
     `Proxy-Authorization: Basic ...`.
   - Read until `\r\n\r\n`, parse the status line, and require a 200 response.
   - If CONNECT fails or times out, close the socket and log `tls.proxy_error`.
   - Only after CONNECT succeeds, start sending the TLS ClientHello as today.

4) Preserve timeout and error semantics.
   - Add `tls_proxy_timeout` (default = `tls_connect_timeout`) or reuse the
     connect timeout for proxy handshake, without changing existing deadlines
     for direct connections.
   - Treat proxy failures like connect failures: log and either return a
     pending correlation ID for soft errors or raise `TransportError` for
     hard failures.

5) Update documentation.
   - Document proxy configuration, CONNECT flow, and limitations in
     `doc/TLS_TRANSPORT.md`.
   - Note that proxies performing TLS validation will still reject the
     single-record handshake.

6) Update unit tests.
   - Add a small HTTP CONNECT proxy fixture that forwards to a local
     TLS handshake server.
   - Cover success, non-200 response, and timeout cases.

## Validation
- Run TLS client/server unit tests with python3.
- Do not run tests/e2e (user-run only).
