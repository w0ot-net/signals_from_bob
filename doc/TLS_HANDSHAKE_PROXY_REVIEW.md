# TLS Handshake Proxy Compatibility Review

## Findings
- Critical: The transport intentionally only exchanges a single ClientHello/ServerHello record and never sends Certificate/ServerKeyExchange/Finished messages, so a proxy that performs certificate validation has nothing to validate and will treat the handshake as incomplete or invalid. This blocks the stated goal when Bob lacks a valid certificate. Evidence: `doc/TLS_TRANSPORT.md:3`, `doc/TLS_TRANSPORT.md:43`, `sfb/transport/tls_handshake/tls_handshake_codec.py:178`, `sfb/transport/tls_handshake/tls_handshake_codec.py:190`, `sfb/transport/tls_handshake/tls_handshake_client.py:178`, `sfb/transport/tls_handshake/tls_handshake_client.py:374`, `sfb/transport/tls_handshake/tls_handshake_server.py:179`, `sfb/transport/tls_handshake/tls_handshake_server.py:251`.
- High: The client and server both reject any extra bytes/records beyond a single TLS Handshake record. A TLS-intercepting proxy that replies with a full TLS ServerHello + certificate chain (or any additional handshake records) will trigger parse errors and connection teardown. Evidence: `doc/TLS_TRANSPORT.md:43`, `sfb/transport/tls_handshake/tls_handshake_client.py:367`, `sfb/transport/tls_handshake/tls_handshake_client.py:374`, `sfb/transport/tls_handshake/tls_handshake_server.py:214`.
- Medium: The generated ClientHello is intentionally minimal (TLS 1.2, no session id, no supported_versions, no signature_algorithms, no supported_groups), and extensions are limited to SNI/ALPN + a private extension. Strict TLS inspectors can flag or reject such a handshake even before certificate validation. Evidence: `sfb/transport/tls_handshake/tls_handshake_codec.py:111`, `sfb/transport/tls_handshake/tls_handshake_codec.py:201`, `sfb/transport/tls_handshake/tls_handshake_codec.py:360`.
- Low: There is no server-side parsing or logging of SNI, so if you are debugging SNI-based allowlisting behavior you cannot confirm what SNI was actually sent from Bob's perspective (the transport treats SNI as cover only). Evidence: `sfb/transport/tls_handshake/tls_handshake_server.py:179`.

## Feasibility
Given the current design, the stated goal is not achievable against a proxy that performs certificate validation. The transport does not present any certificate at all and expects the exchange to end after a single ServerHello. A validating proxy will require a complete TLS handshake (Certificate + key exchange + Finished) with a certificate chain it trusts. Without that, the proxy will terminate the connection even if the SNI is on an allowlist.

The only way this becomes feasible is to change the transport to run a real TLS handshake and present a certificate that the proxy accepts (or to tunnel over a separate TLS terminator that has a valid certificate for the whitelisted SNI). That is a materially different transport than the current single-record ClientHello/ServerHello framing.

## Assumptions
- The proxy performs real TLS validation (not just SNI allowlisting) and expects a complete TLS handshake.
- The proxy is in-path (transparent or explicit) such that it can see and enforce TLS handshakes.
