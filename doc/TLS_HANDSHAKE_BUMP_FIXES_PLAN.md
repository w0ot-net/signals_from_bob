# TLS Handshake Bump Fixes Plan

## Goals
- Clear handshake deadlines once the TLS handshake finishes so response wait uses pending timeout.
- Allow TLS 1.2 negotiation on Python 2 when the OpenSSL build supports it.
- Make pending timeout semantics consistent with sequential phase timing.
- Document the single-record ClientHello limitation.

## Affected Components
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/config.py
- doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md

## Plan
1. Handshake deadline cleanup
   - Clear `handshake_deadline` when the handshake completes.
   - In `_prune_deadlines`, only consider `handshake_deadline` while the handshake is in progress.
   - Leave response waiting bounded by `pending_timeout`.

2. Python 2 TLS 1.2 support
   - Update `_create_ssl_context` to prefer `PROTOCOL_TLS_CLIENT` when available.
   - For Python 2 fallback, use `PROTOCOL_SSLv23` (or `PROTOCOL_TLSv1_2` if present) and disable SSLv2/SSLv3 via context options when supported.
   - Keep certificate verification disabled as before.

3. Pending timeout semantics
   - Treat `tls_bump_pending_timeout` as an end-to-end upper bound.
   - Update validation to require:
     - `pending_timeout >= connect_timeout + handshake_timeout`.
     - If an HTTP proxy is configured, include `proxy_timeout` (defaulting to `connect_timeout`) in the sum.
   - Update defaults in `sfb/config.py` if needed to satisfy the new minimum.
   - Add a short doc note describing the end-to-end meaning of `pending_timeout`.

4. Documentation
   - Add a limitation to `doc/TLS_HANDSHAKE_BUMP_TRANSPORT.md` stating that the ClientHello must fit in a single TLS record (no fragmentation handling).

