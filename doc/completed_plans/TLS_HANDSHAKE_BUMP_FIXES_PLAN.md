# TLS Handshake Bump Fixes Plan

## Goals
- Clear handshake deadlines once the TLS handshake finishes so response wait uses pending timeout.
- Require TLS 1.2 support and fail loudly if the runtime cannot provide it.
- Make pending timeout semantics consistent with sequential phase timing.
- Document the single-record ClientHello limitation.

## Affected Components
- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/config.py
- doc/completed_plans/TLS_HANDSHAKE_BUMP_TRANSPORT.md

## Plan
1. Handshake deadline cleanup
   - Clear `handshake_deadline` when the handshake completes.
   - In `_prune_deadlines`, only consider `handshake_deadline` while the handshake is in progress.
   - Leave response waiting bounded by `pending_timeout`.

2. TLS 1.2 requirement
   - Detect TLS 1.2 availability (`ssl.HAS_TLSv1_2` or `ssl.PROTOCOL_TLSv1_2`).
   - Fail loudly with a `TransportError` if TLS 1.2 is not available (enforce in `_create_ssl_context`).
   - Keep certificate verification disabled as before.

3. Pending timeout semantics
   - Remove `tls_bump_pending_timeout` as a config option.
   - Compute `pending_timeout` internally as:
     - `connect_timeout + handshake_timeout`.
     - If an HTTP proxy is configured, add `proxy_timeout` (defaulting to `connect_timeout`).
   - Add a short doc note describing the end-to-end meaning of the computed timeout.

4. Documentation
   - Add a limitation to `doc/completed_plans/TLS_HANDSHAKE_BUMP_TRANSPORT.md` stating that the ClientHello must fit in a single TLS record (no fragmentation handling).

## Execution Notes
- Cleared TLS handshake deadlines after completion and ignored them once the handshake is done so pending timeouts govern response waits.
- Enforced TLS 1.2 availability checks in the TLS bump client SSL context creation.
- Documented the computed pending timeout semantics and the single-record ClientHello limitation.
