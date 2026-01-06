# TLS Handshake Compatibility Plan

## Context
We need the TLS ClientHello transport to pass proxies that only inspect the
ClientHello (SNI allowlist) and then pass TCP through. The current ClientHello
is minimal and uses a private extension for payload, which can trigger
inspection rejects. The goal is to make the ClientHello look ordinary and
remove private extensions while still carrying SFB payloads in the single
ClientHello/ServerHello exchange.

## Goals
- Make the ClientHello appear like a typical TLS 1.2 ClientHello while still
  carrying SFB payload bytes.
- Remove private extensions entirely and use a standard extension for payload
  encoding in both directions.
- Keep the transport flow as a single ClientHello and single ServerHello per
  connection, with no certificate exchange.
- Preserve Python 2.7/3 compatibility and ASCII-only code.

## Non-goals
- Implement a full TLS handshake or certificate validation.
- Add new transports or change the tunnel protocol outside TLS handshake.
- Add configuration switches to keep the private extension path.

## Affected components
- sfb/transport/tls_handshake/tls_handshake_codec.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake/tls_handshake_server.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- doc/TLS_TRANSPORT.md
- tests/test_tls_codec.py
- tests/test_tls_client_server.py

## Plan
1) Define a standard payload extension and remove the private extension.
   - Replace EXT_SFB_DATA usage with a standard extension (session_ticket,
     0x0023) that allows opaque data.
   - Rename helpers to reflect the new payload extension and delete the
     private extension constant and related parse errors.
   - Keep deterministic extension ordering for stable overhead sizing.

2) Make the ClientHello look ordinary.
   - Allow non-zero session_id_len (0-32) and include a random session_id in
     builds to match common client behavior.
   - Add standard TLS 1.2 extensions with sane defaults:
     supported_groups (secp256r1, secp384r1), ec_point_formats (uncompressed),
     signature_algorithms (common RSA/ECDSA + SHA256/384),
     extended_master_secret, renegotiation_info.
   - Keep SNI and ALPN optional and include them in a typical order before the
     payload extension.

3) Update ServerHello to mirror ordinary server behavior.
   - Allow non-zero session_id_len and include a random session_id.
   - Include standard server extensions (extended_master_secret,
     renegotiation_info, and ALPN selection when configured).
   - Encode the response payload in the same standard payload extension.

4) Recompute MTU and overhead calculations.
   - Update calc_clienthello_payload_cap and calc_serverhello_payload_cap to
     build empty records with the new extension set so payload sizing is
     accurate with the added extensions and optional SNI/ALPN.
   - Keep validation errors clear when configured sizes cannot fit a packet.

5) Update documentation.
   - Update doc/TLS_TRANSPORT.md to describe the new extension set, session_id
     handling, and the standard payload extension.
   - Note the breaking change: old private-extension peers are incompatible.

6) Update unit tests.
   - Adjust codec tests to expect the standard payload extension and new
     session_id behavior.
   - Add coverage for parsing with extra standard extensions and for rejecting
     missing/duplicate payload extensions.

## Validation
- Run the existing TLS codec and client/server unit tests with python3.
- Do not run tests/e2e (user-run only).

## Execution Notes
- Updated TLS handshake codec to use session_ticket payloads, added standard
  extensions, and enabled non-zero session_id handling.
- Added ALPN selection in ServerHello when configured and recalculated MTU
  overhead sizing with the new extension set.
- Updated TLS transport documentation and unit tests.
- Ran `python3 -m unittest tests.test_tls_codec tests.test_tls_client_server`
  (2 tests skipped).
