# TLS Handshake Bump Transport

## Overview

The TLS handshake bump transport is a low-bandwidth covert channel that
relies on a TLS-bumping proxy that leaks upstream certificate metadata
in its HTTPS error pages.

- Alice encodes requests in the SNI value of the TLS ClientHello.
- Bob encodes responses in the CN of the upstream certificate.
- The bumping proxy connects to Bob, fails validation, and returns an
  HTTPS error page that includes the CN, which Alice extracts.

This transport is designed for intercepting proxies that validate upstream
certificates and expose CN details in their error pages. It is intentionally
small and fragile by design.

## Encoding

All payloads use the same format before base32 encoding:

```
version (1 byte) | payload_len (2 bytes, big-endian) | payload
```

The encoded string is base32 (RFC 4648), lowercase, with padding stripped.

### SNI (Alice -> Bob)

- Payload is base32-encoded and split into DNS labels (<= 63 chars).
- Labels are joined with dots, then the base domain is appended.
- The full name must be <= 253 chars.

Example:

```
<base32 labels>.<base_domain>
```

### CN (Bob -> Alice)

- Payload is base32-encoded into a single CN string.
- The CN length cap is configured by `tls_bump_max_cn_len`.

## MTU and Asymmetry

The transport computes independent MTUs per direction:

- Alice -> Bob MTU is limited by the SNI max name length and base domain size.
- Bob -> Alice MTU is limited by the CN max length.

Each side exposes `send_mtu` and `recv_mtu` separately to support asymmetric
MTU negotiation.

## Client Flow (Alice)

1. Connect to the bumping proxy (`tls_bump_target`).
2. Optional HTTP CONNECT proxy is supported (`tls_bump_http_proxy`).
3. Start TLS with SNI set to the encoded request subdomain.
4. Send a minimal HTTPS request (`GET <path>`) to trigger an error page.
5. Extract CN from the error page via `tls_bump_cn_regex`.
6. Decode CN to response bytes.

TLS certificate verification is disabled for the proxy connection.

## Server Flow (Bob)

1. Accept TCP connections from the bumping proxy.
2. Parse ClientHello and extract SNI.
3. Decode SNI to request bytes.
4. Select a certificate whose CN equals the encoded response payload.
5. Send a minimal TLS 1.2 handshake:
   ServerHello, Certificate, ServerHelloDone, then close.

The server does not complete a full TLS handshake; it only needs to deliver
the certificate so the proxy can read CN and fail validation.

## Certificates

Bob expects certificates in `tls_bump_cert_dir` with filenames keyed by CN:

- `<cn>.der` (preferred, raw DER bytes)
- `<cn>.pem` (base64 PEM, converted to DER at load time)

If `tls_bump_cert_helper` is set, the server will invoke it when a cert is
missing:

```
<helper_path> <cn> <out_der_path>
```

An example helper is provided in `scripts/tls_bump_cert_helper.py`
(requires `openssl` in PATH).

## Configuration Summary

Client:
- `tls_bump_target` (host:port, bumping proxy)
- `tls_bump_base_domain` (default `example.com`)
- `tls_bump_http_proxy` / `tls_bump_http_proxy_auth` (optional)
- `tls_bump_request_path` (default `/`)
- `tls_bump_cn_regex` (capture group required)
- `tls_bump_max_cn_len` (CN length cap)

Server:
- `tls_bump_listen_addr`
- `tls_bump_base_domain` (default `example.com`, must match client)
- `tls_bump_cert_dir` (required)
- `tls_bump_cert_helper` (optional)
- `tls_bump_max_clienthello_bytes` (record size cap)

## Limitations

- Very low throughput by design.
- Requires a TLS-bumping proxy that exposes CN details in error pages.
- No robustness if the proxy does not leak CN.
- Not a full TLS implementation; it is a covert channel.
