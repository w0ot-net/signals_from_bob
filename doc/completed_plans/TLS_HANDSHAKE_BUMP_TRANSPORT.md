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

Request and response tokens are base32 (RFC 4648), lowercase, with padding
stripped. The request and response frames differ.

### Request frame (SNI)

```
version (1 byte) | payload_len (2 bytes, big-endian) | payload
```

### Response frame (CN)

```
version (1 byte) | payload_len (2 bytes, big-endian) |
checksum (2 bytes, CRC32 truncated) | payload
```

The checksum is computed over the payload bytes.

### SNI (Alice -> Bob)

- Payload is base32-encoded and split into DNS labels (<= 63 chars).
- Labels are joined with dots, then the base domain is appended.
- The full name must be <= 253 chars.

Example:

```
<base32 labels>.<base_domain>
```

### CN (Bob -> Alice)

- Payload uses the response frame (length + checksum), then base32-encoded.
- The CN token is padded with base32 "a" to a fixed length defined by the
  in-memory certificate template; the decoder tolerates trailing zero bytes.

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
5. Extract the response token via scan or regex.
6. Decode the response token to bytes.

TLS certificate verification is disabled for the proxy connection.

Scan mode searches for base32 tokens and validates the response frame; regex
mode uses `tls_bump_response_regex` to capture the token.

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

The certificate template lives in a dedicated Python module that only exposes
the base64-encoded DER bytes (plus any fixed constants like CN length/offsets).
A generator script owns updates to that file, so other code only imports the
template bytes and patches the CN placeholder at runtime.

Bob builds a DER certificate in memory from the fixed template that contains a
CN placeholder. The server pads the encoded CN to the template length and
patches the placeholder before sending the handshake record. No certificate
directory or helper is used at runtime. The template CN length is 256 to raise
the response MTU; larger CNs may be less compatible with some proxy error
pages.

## Configuration Summary

Client:
- `tls_bump_target` (host:port, bumping proxy)
- `tls_bump_base_domain` (default `example.com`)
- `tls_bump_http_proxy` / `tls_bump_http_proxy_auth` (optional)
- `tls_bump_request_path` (default `/`)
- `tls_bump_response_mode` (`scan` or `regex`, default `scan` if no regex)
- `tls_bump_response_regex` (capture group for base32 token, optional)

Server:
- `tls_bump_listen_addr`
- `tls_bump_base_domain` (default `example.com`, must match client)
- `tls_bump_max_clienthello_bytes` (record size cap)

## Limitations

- Very low throughput by design.
- Requires a TLS-bumping proxy that exposes CN details in error pages.
- No robustness if the proxy does not leak CN.
- Not a full TLS implementation; it is a covert channel.

## Execution Notes

- Transport implementation already existed; aligned cert handling with the
  data-only template module requirement.
- Added a dedicated certificate builder module that decodes the base64
  template and patches CN bytes at runtime.
- Updated the server path and cert template tests to use the new builder.
