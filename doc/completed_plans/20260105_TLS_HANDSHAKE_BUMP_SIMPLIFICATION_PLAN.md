# TLS Handshake Bump Simplification Plan

Status: completed

## Summary

Reduce duplication in the TLS bump and TLS handshake client state handling,
consolidate TLS bump base domain validation, and centralize TLS handshake SNI
validation while preserving existing wire behavior, timeouts, and platform
compatibility.

## Goals

- Introduce a single explicit phase state for client connections to simplify
  select/timeout/read/write logic.
- Apply the same phase/state simplification to the TLS handshake client.
- Centralize base domain validation by using the codec helper.
- Centralize TLS handshake SNI validation via a shared codec helper.
- Preserve timing semantics, MTU limits, and error handling behavior.
- Maintain Python 2.7/3 compatibility and Windows/Linux support.

## Non-Goals

- Changing TLS bump wire format or payload framing.
- Altering config defaults or adding new configuration options.
- Reworking transport architecture (no threads/async).
- Running E2E tests (user-owned).

## Affected Components

- sfb/transport/tls_handshake_bump/tls_handshake_bump_client.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py
- sfb/transport/tls_handshake/tls_handshake_client.py
- sfb/transport/tls_handshake/tls_handshake_config.py
- sfb/transport/tls_handshake/tls_handshake_codec.py
- tests/test_tls_client_server.py

## Plan

1. Add explicit client phases (TLS bump and TLS handshake)
   - Define phase constants (connect, proxy, handshake, request, response).
   - Extend `_PendingConn` with a `phase` field initialized at creation.
   - Add helpers for phase-specific deadlines and read/write interests.

2. Centralize client state driving (TLS bump and TLS handshake)
   - Replace `_handle_readable`/`_handle_writable` with a single driver that
     advances state based on phase and readiness flags.
   - Update `_select_timeout`, `_build_select_lists`, and `_prune_deadlines`
     to use the phase helper instead of repeated branching.
   - Preserve current timeout scheduling (connect, proxy, handshake, pending)
     and existing logging/error behavior.

3. Consolidate base domain validation (TLS bump)
   - Replace `_validate_base_domain` with `codec.normalize_domain`.
   - Wrap `ValueError` as `TransportError` and, if needed, map error messages
     to current `tls_bump_base_domain ...` strings to avoid regressions.
   - Keep existing ASCII/non-empty checks if they are still needed to preserve
     error wording.

4. Consolidate TLS handshake SNI validation
   - Add a shared SNI normalization/validation helper in
     `tls_handshake_codec.py`.
   - Replace `_validate_sni` in `tls_handshake_config.py` with the helper,
     wrapping `ValueError` as `TransportError`.
   - Preserve current error semantics where possible to avoid test churn.

5. Update tests only if needed
   - If any tests depend on exact error strings or behavior, update them to
     reflect the consolidated validation path.
   - Adjust TLS handshake client tests if the `_PendingConn` surface changes.

## Validation

- Run `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Run `python3 -m unittest tests.test_tls_handshake_bump_codec`.
- Run `python3 -m unittest tests.test_tls_client_server`.
- Do not run tests in `tests/e2e/`.

## Execution Notes

- Implemented phase-based client state handling in TLS bump and TLS handshake clients.
- Centralized base domain and SNI validation through codec helpers with error mapping.
- Ran `python3 -m unittest tests.test_tls_handshake_bump_client_server`.
- Ran `python3 -m unittest tests.test_tls_handshake_bump_codec`.
- Ran `python3 -m unittest tests.test_tls_client_server` (skipped=5).
