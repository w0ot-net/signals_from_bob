# TLS Handshake Squid Transport Plan

## Context
We want a new transport that can pass through HTTP proxies running Squid with
ssl_bump bump, using handshake metadata instead of application data. The idea
is to encode Alice -> Bob requests in SNI (base32 subdomain data) and encode
Bob -> Alice responses in the upstream certificate CN that Squid echoes in
its TLS error page when cert validation fails. This is intentionally low
bandwidth and Squid-specific.

## Goals
- Provide a new transport named `tls_handshake_squid` that works through Squid
  ssl_bump bump with upstream cert validation enabled.
- Encode requests in SNI using base32 without padding, staying within DNS name
  limits and configured base domain.
- Decode responses from Squid error pages by extracting the upstream cert CN
  and base32-decoding it.
- Preserve tunnel asymmetry (Alice initiates, Bob responds).
- Keep Python 2.7/3 compatibility and use only the standard library.

## Non-Goals
- High throughput or large MTU; this transport is expected to be very small.
- General MITM proxy compatibility; the behavior is Squid-specific.
- Full TLS correctness end-to-end; this is a covert channel, not a normal TLS
  transport.
- Robust operation if Squid does not leak CN details in error pages.

## Affected Components
- `sfb/transport/tls_handshake_squid/` (new transport package)
- `sfb/transport/tls_handshake_squid/tls_handshake_squid_client.py`
- `sfb/transport/tls_handshake_squid/tls_handshake_squid_server.py`
- `sfb/transport/tls_handshake_squid/tls_handshake_squid_codec.py`
- `sfb/transport/tls_handshake_squid/tls_handshake_squid_config.py`
- `sfb/transport/__init__.py` (transport registry)
- `sfb/config.py` (new config fields)
- `sfb/cli.py` (new CLI args)
- `doc/TRANSPORTS.md`
- `doc/TLS_HANDSHAKE_SQUID_TRANSPORT.md` (new spec doc)
- `tests/test_tls_handshake_squid_codec.py`
- `tests/test_tls_handshake_squid_client_server.py`
- `scripts/` (optional helper for cert generation; stdlib + openssl subprocess)

## Plan
1) Define SNI and CN encoding rules and MTU calculation.
   - Base32 encode packet bytes without padding (strip '='), lowercase only.
   - Encode into DNS labels <= 63 chars, joined by dots, then append a fixed
     base domain (config `tls_squid_base_domain`).
   - Add a short header in the encoded data: version byte + payload length to
     allow safe decode without padding.
   - Compute max payload based on base domain length and DNS limits, and
     validate it can carry at least `PACKET_HEADER_SIZE + 1` bytes.
   - Define CN encoding to mirror SNI encoding (base32, no padding) and
     document the max CN length to compute Bob -> Alice MTU.

2) Client transport (Alice) using real TLS to the proxy.
   - Reuse the HTTP CONNECT proxy flow from `tls_handshake` for TCP setup.
   - Use `ssl` to complete a normal TLS handshake with the proxy so it can
     return an HTTPS error page.
   - Set SNI to the encoded request subdomain + base domain.
   - Send a minimal HTTPS request (e.g., `GET /` with Host header) to trigger
     Squid's upstream connect and error generation.
   - Read the HTTP response body and extract CN using a configurable regex
     (default matches Squid's "Self-signed SSL Certificate: /CN=..." line).
   - Decode CN base32 to response bytes and return them to the tunnel.

3) Server transport (Bob) for Squid upstream connections.
   - Accept TCP connections and parse ClientHello to extract SNI data.
   - Decode SNI to the request payload and pass it to the tunnel.
   - Select a certificate for the response payload via a cert provider:
     - Option A: a pre-generated cert directory keyed by base32 payload.
     - Option B: a subprocess helper that runs `openssl` to generate a cert
       for the payload on demand (cached on disk).
   - Send a minimal TLS 1.2 server handshake sequence:
     ServerHello (RSA cipher), Certificate (DER bytes), ServerHelloDone,
     then close. This should be enough for Squid to parse the CN and fail
     validation, producing an error page for Alice.

4) Configuration and validation.
   - Add config for base domain, proxy address/auth/timeout, request path,
     CN extraction regex, cert directory, and optional openssl helper path.
   - Enforce ASCII-only inputs and length constraints.
   - Ensure asymmetric MTU values are computed per direction (SNI vs CN).

5) Documentation.
   - Add a new transport spec doc describing assumptions, encoding, limits,
     and required Squid bump settings.
   - Update `doc/TRANSPORTS.md` and any proxy-related notes.

6) Tests.
   - Unit tests for base32 encode/decode, label splitting, and MTU caps.
   - Client parsing test with a captured Squid error page snippet.
   - Server handshake build test that includes a sample DER cert.

## Validation
- Run `python3 -m unittest tests.test_tls_handshake_squid_codec`
- Run `python3 -m unittest tests.test_tls_handshake_squid_client_server`
- Do not run tests/e2e (user-run only).
