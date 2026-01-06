# TLS Handshake Realism Plan

## Summary
Enhance the TLS ClientHello/ServerHello look to better match common TLS 1.2
handshakes while preserving the single-record transport model and session_ticket
payload carrier.

## Goals
- Expand cipher suite, group, and signature algorithm lists to align with common
  TLS 1.2 clients.
- Add common ClientHello extensions (status_request, signed_certificate_timestamp,
  padding) with deterministic ordering.
- Keep the payload in the session_ticket extension for both directions.
- Maintain Python 2.7/3 compatibility, ASCII-only code, and standard library use.
- Preserve asymmetric MTU calculations with accurate overhead sizing.

## Non-Goals
- Implement full TLS negotiation or certificate validation.
- Add TLS 1.3 support or encrypted extensions.
- Change tunnel semantics outside the TLS handshake transport.

## Affected Components
- `sfb/transport/tls_handshake/tls_handshake_codec.py`
- `sfb/transport/tls_handshake/tls_handshake_config.py`
- `sfb/config.py`
- `sfb/cli.py`
- `doc/TLS_TRANSPORT.md`
- `doc/TLS_HANDSHAKE_PROXY_REVIEW.md`
- `tests/test_tls_codec.py`
- `tests/test_tls_client_server.py`

## Plan
1) Expand cipher suites with common TLS 1.2 choices.
   - Add AES_256_GCM and CHACHA20_POLY1305 ECDHE suites.
   - Add a limited CBC fallback set to mimic legacy clients.
   - Keep deterministic ordering and document the list in `doc/TLS_TRANSPORT.md`.

2) Expand supported_groups and signature_algorithms.
   - Add x25519 to supported_groups (keep secp256r1/secp384r1).
   - Add RSA-PSS signature algorithms (sha256/sha384/sha512) alongside the
     existing RSA/ECDSA set.

3) Add common ClientHello extensions.
   - status_request (OCSP stapling) with empty responder and request lists.
   - signed_certificate_timestamp with zero-length data.
   - padding (RFC 7685) to reach a configured target length.

4) Add a padding configuration knob.
   - Introduce a config option such as `tls_clienthello_padding_target` with a
     default of 0 (disabled) to avoid unexpected MTU loss.
   - Allow CLI override for users that prioritize realism over MTU.
   - Ensure padding length is at least 1 when enabled, and only zero bytes.

5) Reorder extensions to match common TLS 1.2 ordering.
   - Proposed ClientHello order: SNI, extended_master_secret,
     renegotiation_info, supported_groups, ec_point_formats, session_ticket
     (payload), signature_algorithms, status_request, ALPN,
     signed_certificate_timestamp, padding (if enabled).
   - Proposed ServerHello order: extended_master_secret, renegotiation_info,
     ALPN (if configured), session_ticket (payload).
   - Keep parsing tolerant of any order while enforcing a single payload
     extension.

6) Update MTU calculations and validation.
   - MTU helpers must build a realistic empty ClientHello/ServerHello with the
     new extension set and optional padding to compute payload caps.
   - Reject configurations where overhead exceeds the configured record size.

7) Update docs and tests.
   - Document the new defaults, extension set, and padding behavior.
   - Refresh proxy review notes to remove references to the old minimal
     ClientHello.
   - Update codec tests for new extensions, ordering, and padding length rules.

## Validation
- Run `python3 -m unittest tests.test_tls_codec tests.test_tls_client_server`.
- Do not run tests/e2e (user-run only).

## Execution Notes
- Expanded cipher suites, groups, signature algorithms, and ClientHello extensions
  in `sfb/transport/tls_handshake/tls_handshake_codec.py`.
- Added ClientHello padding target config/CLI handling and updated MTU overhead
  calculations in `sfb/transport/tls_handshake/tls_handshake_config.py`,
  `sfb/transport/tls_handshake/tls_handshake_client.py`, `sfb/config.py`,
  and `sfb/cli.py`.
- Updated docs and tests for the new extension order, padding behavior, and
  defaults.
- Ran `python3 -m unittest tests.test_tls_codec tests.test_tls_client_server`.
